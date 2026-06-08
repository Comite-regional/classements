#!/usr/bin/env python3
"""
Synchronisation des classements FFTA → fichiers JSON
pour la page de classements Pays de la Loire (CR12).

Usage:
  python fetch_ffta.py

Variables d'environnement requises:
  FFTA_SESSION_IDENTITE  : ton identifiant FFTA (ex: "0123456")
  FFTA_SAISON            : année de saison (ex: "2026"), défaut = année courante

Optionnel:
  FFTA_BASE_URL          : URL de base de l'API (défaut prod)
  FFTA_OUTPUT_DIR        : dossier de sortie des JSON (défaut: ./data)

Les URLs exactes sont à vérifier dans la documentation Postman :
  https://documenter.getpostman.com/view/6393466/UV5XjJQo
"""

import os
import json
import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

try:
    import requests
except ImportError:
    raise SystemExit("Installe le module requests : pip install requests")

# ─── Configuration ───────────────────────────────────────────────────────────

SESSION_IDENTITE = os.environ.get("FFTA_SESSION_IDENTITE", "")
BASE_URL = os.environ.get("FFTA_BASE_URL", "https://extranet.ffta.fr/ws/rest").rstrip("/")
SAISON = os.environ.get("FFTA_SAISON", str(datetime.now().year))
OUTPUT_DIR = Path(os.environ.get("FFTA_OUTPUT_DIR", "data"))

# Ligue cible : CR12 = Pays de la Loire
LIGUE_CODE = "CR12"
# Départements PDL (pour filtrage de secours si region_code absent)
DEPTS_PDL = {"44", "49", "53", "72", "85"}

# Disciplines à récupérer : code API → nom de fichier JSON de sortie
DISCIPLINES = {
    "S": "Tir 18m.json",       # Tir en Salle 18m
    "T": "TAE.json",            # Tir à l'Arc Extérieur (I + N)
    "C": "Campagne.json",       # Campagne
    "N": "Nature.json",         # Nature
    "3": "3D.json",             # 3D
    "H": "para ext.json",       # Para-tir extérieur
    "I": "Para salle 18m.json", # Para-tir 18m
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ─── Auth ────────────────────────────────────────────────────────────────────

def make_password() -> str:
    """Le mot de passe est la date/heure Paris au format YYYYMMDDHHMM.
    Important : le serveur FFTA est en heure de Paris (UTC+1/+2).
    GitHub Actions tourne en UTC → sans correction, le décalage de 2h
    ferait échouer l'authentification (tolérance serveur : ±5 min seulement).
    """
    paris_time = datetime.now(ZoneInfo("Europe/Paris"))
    return paris_time.strftime("%Y%m%d%H%M")


def get_token(session: requests.Session) -> str:
    """Obtient un token valide 1h.

    NOTE : l'URL exacte est à vérifier dans la doc Postman.
    Essaie les deux variantes courantes.
    """
    url = f"{BASE_URL}/Classements/GetToken"
    params = {
        "sessionIdentite": SESSION_IDENTITE,
        "password": make_password(),
        "format": "json",
    }
    log.info("GetToken → %s (sessionIdentite=%s...)", url, SESSION_IDENTITE[:4] if SESSION_IDENTITE else "??")
    try:
        resp = session.get(url, params=params, timeout=30)
        log.info("Réponse HTTP %s", resp.status_code)
        log.info("Réponse brute : %s", resp.text[:500])
        resp.raise_for_status()
        data = resp.json()
        token = (
            data.get("Response", {}).get("token")
            or data.get("Response", {}).get("Token")
            or data.get("token")
            or data.get("Token")
        )
        if token:
            log.info("Token obtenu ✓")
            return token
        raise RuntimeError(f"Réponse OK mais token absent : {data}")
    except Exception as e:
        raise RuntimeError(
            f"Impossible d'obtenir un token FFTA.\n"
            f"URL : {url}\n"
            f"Erreur : {e}\n"
            f"Vérifie le secret FFTA_SESSION_IDENTITE dans GitHub → Settings → Secrets."
        )


# ─── Appels API ──────────────────────────────────────────────────────────────

def api_get(session: requests.Session, endpoint: str, token: str, **params) -> dict:
    url = f"{BASE_URL}/{endpoint}"
    resp = session.get(url, params={"token": token, **params}, timeout=60)
    resp.raise_for_status()
    return resp.json()


def get_classements_list(session, token, disc_code) -> list[dict]:
    """Retourne la liste des classements disponibles pour une discipline."""
    data = api_get(
        session, "Classements/Classements", token,
        SaisonAnnee=SAISON,
        DisciplineCode=disc_code,
    )
    log.info("  Réponse brute GetClassements(%s) : %s", disc_code, str(data)[:600])
    response = data.get("Response", {})
    # Cherche dans toutes les clés possibles
    for key in ("ClassementsArray", "Classements", "classements", "items", "data"):
        val = response.get(key)
        if val:
            return val
    # Si Response est directement une liste
    if isinstance(response, list):
        return response
    log.warning("  Aucune liste trouvée dans Response. Clés disponibles : %s", list(response.keys()))
    return []


def get_classement_detail(session, token, classement_id) -> list[dict]:
    """Retourne tous les archers d'un classement donné."""
    data = api_get(
        session, "Classements/Classement", token,
        Classement=classement_id,
    )
    log.info("  Réponse brute GetClassement(%s) : %s", classement_id, str(data)[:600])
    response = data.get("Response", {})
    # Cherche dans toutes les clés possibles
    for key in ("ClassementArray", "Participants", "Archers", "archers", "classement", "items", "data"):
        val = response.get(key)
        if val:
            return val
    if isinstance(response, list):
        return response
    log.warning("  Aucune liste d'archers. Clés : %s", list(response.keys()))
    return []


# ─── Filtrage région ──────────────────────────────────────────────────────────

def dept_from_club_code(code: str) -> str:
    """Extrait le département depuis un code structure FFTA (ex: '0440001' → '44')."""
    digits = "".join(c for c in str(code or "") if c.isdigit())
    if len(digits) >= 4:
        return digits[2:4]
    return ""


def is_pdl(archer: dict) -> bool:
    """Retourne True si l'archer appartient à la région PDL (CR12)."""
    # 1. Via region_code direct
    if archer.get("region_code", "").upper() == LIGUE_CODE:
        return True
    # 2. Via LigueCode dans les classements d'épreuve
    if archer.get("LigueCode", "").upper() == LIGUE_CODE:
        return True
    # 3. Via département (extrait du code de structure/club)
    club_code = (
        archer.get("club_code")
        or archer.get("CODE_STRUCTURE")
        or archer.get("StructureCode")
        or ""
    )
    dept = dept_from_club_code(str(club_code))
    if dept in DEPTS_PDL:
        return True
    # 4. Via departement_code direct
    dept2 = str(archer.get("departement_code", "") or "").replace("000", "").strip()
    if dept2 in DEPTS_PDL:
        return True
    return False


# ─── Normalisation des données ────────────────────────────────────────────────

def get_cat(archer: dict) -> str:
    """Retourne la catégorie d'âge la plus pertinente."""
    for key in ("categorie", "Categorie", "ClassementAge", "categorie_age"):
        v = archer.get(key, "")
        if isinstance(v, str) and v:
            return v
        if isinstance(v, dict):
            # Prend la première catégorie si dict {code: libellé}
            keys = list(v.keys())
            return keys[0] if keys else ""
    return ""


def get_scores(archer: dict) -> tuple[str, str, str, str]:
    """Retourne (score1, score2, score3, moyenne)."""
    scores_raw = archer.get("scores", {})
    if isinstance(scores_raw, dict):
        s1 = str(scores_raw.get("1", "") or "")
        s2 = str(scores_raw.get("2", "") or "")
        s3 = str(scores_raw.get("3", "") or "")
    else:
        s1 = str(archer.get("SCORE_DIST1", "") or archer.get("score1", "") or "")
        s2 = str(archer.get("SCORE_DIST2", "") or archer.get("score2", "") or "")
        s3 = str(archer.get("SCORE_DIST3", "") or archer.get("score3", "") or "")

    moy_raw = (
        archer.get("moyenne")
        or archer.get("score_total")
        or archer.get("MOY_SCORE")
        or ""
    )
    moy = str(moy_raw) if moy_raw else ""
    # Calcule la moyenne si absente mais les 3 scores sont présents
    if not moy and s1 and s2 and s3:
        try:
            moy = str(round((float(s1) + float(s2) + float(s3)) / 3, 2))
        except ValueError:
            moy = ""
    return s1, s2, s3, moy


def get_club_info(archer: dict) -> tuple[str, str, str]:
    """Retourne (NOM_STRUCTURE, NOM_ABREGE, CODE_STRUCTURE)."""
    nom = (
        archer.get("club")
        or archer.get("NOM_STRUCTURE")
        or archer.get("Club")
        or ""
    )
    abr = archer.get("NOM_ABREGE", "") or ""
    code = (
        archer.get("club_code")
        or archer.get("CODE_STRUCTURE")
        or archer.get("StructureCode")
        or ""
    )
    return str(nom), str(abr), str(code)


def normalize_archer(archer: dict, disc_code: str, rank: int) -> dict:
    """Convertit un archer de l'API vers la structure attendue par le HTML."""
    s1, s2, s3, moy = get_scores(archer)
    nom_struct, nom_abr, code_struct = get_club_info(archer)

    row = {
        "Rang_ligue": rank,
        "RANG": rank,
        "NO_LICENCE": str(archer.get("licence_code") or archer.get("LicenceCode") or ""),
        "NOM_PERSONNE": str(archer.get("nom") or archer.get("Nom") or "").upper(),
        "PRENOM_PERSONNE": str(archer.get("prenom") or archer.get("Prenom") or "").capitalize(),
        "SEXE": str(archer.get("sexe_code") or archer.get("sexe") or archer.get("SEXE") or ""),
        "ARME": str(archer.get("arme_code") or archer.get("arme") or archer.get("ARME") or ""),
        "CAT": get_cat(archer),
        "CATEGORIE": get_cat(archer),
        "NOM_STRUCTURE": nom_struct,
        "NOM_ABREGE": nom_abr,
        "CODE_STRUCTURE": code_struct,
        "SCORE1": s1,
        "SCORE2": s2,
        "SCORE3": s3,
        "MOY_SCORE": moy,
        "DISCIPLINE": disc_code,
    }

    # TAE : champ TYPE (I = international, N = national)
    tae_type = archer.get("type") or archer.get("TYPE") or ""
    if disc_code == "T" and tae_type:
        row["TYPE"] = str(tae_type).upper()

    # Para / TAE : distances et blasons
    for fld in ("distance", "DISTANCE", "blason", "BLASON"):
        if archer.get(fld):
            row[fld.lower()] = str(archer[fld])

    return row


# ─── Logique principale ────────────────────────────────────────────────────────

def fetch_discipline(session: requests.Session, token: str, disc_code: str) -> list[dict]:
    """Récupère tous les archers PDL pour une discipline donnée."""
    log.info("→ Discipline %s  (saison %s)", disc_code, SAISON)

    try:
        classements = get_classements_list(session, token, disc_code)
    except Exception as e:
        log.error("  Erreur GetClassements(%s): %s", disc_code, e)
        return []

    if not classements:
        log.warning("  Aucun classement trouvé pour %s", disc_code)
        return []

    log.info("  %d classement(s) trouvé(s)", len(classements))

    all_rows: list[dict] = []

    for cl in classements:
        cl_id = cl.get("id") or cl.get("Id") or cl.get("ID") or cl.get("classement_id")
        cl_name = cl.get("libelle") or cl.get("Libelle") or cl.get("nom") or cl_id

        # Filtre : ne prend que les classements de niveau Ligue/Région (ou tous si non précisé)
        niveau = str(cl.get("TypeNiveau") or cl.get("niveau") or "").upper()
        if niveau and niveau not in ("", "L", "R", "LIGUE", "REGIONAL", "REGION", "CR"):
            # Classement national uniquement : on le prend quand même
            # (on filtrera les archers PDL ensuite)
            pass

        if not cl_id:
            log.debug("  Classement sans ID ignoré: %s", cl)
            continue

        try:
            archers = get_classement_detail(session, token, cl_id)
        except Exception as e:
            log.warning("  Erreur GetClassement(%s): %s", cl_id, e)
            continue

        log.info("  %-60s  %3d archers", str(cl_name)[:60], len(archers))

        for rank_in_cl, archer in enumerate(archers, start=1):
            # Récupère le rang ligue depuis l'API ou recalcule
            rang = (
                archer.get("rang_ligue")
                or archer.get("place_ligue")
                or archer.get("place")
                or rank_in_cl
            )
            row = normalize_archer(archer, disc_code, int(str(rang).strip() or rank_in_cl))
            row["_classement_nom"] = str(cl_name)
            all_rows.append(row)

        # Petite pause pour ne pas surcharger le serveur
        time.sleep(0.3)

    return all_rows


def run():
    if not SESSION_IDENTITE:
        raise SystemExit(
            "Variable d'environnement FFTA_SESSION_IDENTITE non définie.\n"
            "Lance : export FFTA_SESSION_IDENTITE='ton_code_utilisateur'"
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    meta = {"generated_at": datetime.utcnow().isoformat() + "Z", "saison": SAISON, "ligue": LIGUE_CODE}

    session = requests.Session()
    session.headers["Accept"] = "application/json"

    log.info("Obtention du token FFTA…")
    token = get_token(session)

    results: dict[str, list] = {}

    for disc_code, filename in DISCIPLINES.items():
        rows = fetch_discipline(session, token, disc_code)
        results[disc_code] = rows

        out_path = OUTPUT_DIR / filename
        payload = {"meta": meta, "rows": rows}
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("  ✓ %s  (%d lignes)", out_path, len(rows))

    # Fichier méta global (date de mise à jour)
    meta_path = OUTPUT_DIR / "meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Terminé. Fichiers écrits dans %s/", OUTPUT_DIR)


if __name__ == "__main__":
    run()
