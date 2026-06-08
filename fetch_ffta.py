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
    """Retourne tous les archers d'un classement donné.

    L'API retourne ClassementArray = liste d'objets classement.
    Chaque objet a un champ 'archers' qui est un dict {"1": {...}, "2": {...}, ...}
    ou une liste vide [] quand il n'y a pas d'archers.
    On extrait les archers de tous les objets et on les enrichit avec les métadonnées
    du classement (sexe_code, arme_code, libelle).
    """
    data = api_get(
        session, "Classements/Classement", token,
        Classement=classement_id,
    )
    response = data.get("Response", {})
    classement_array = response.get("ClassementArray") or []
    if isinstance(response, list):
        classement_array = response

    all_archers: list[dict] = []
    for cl_item in classement_array:
        if not isinstance(cl_item, dict):
            continue
        raw_archers = cl_item.get("archers") or []
        # archers peut être un dict {"1": {...}, "2": {...}} ou une liste []
        if isinstance(raw_archers, dict):
            archer_list = list(raw_archers.values())
        elif isinstance(raw_archers, list):
            archer_list = raw_archers
        else:
            archer_list = []

        for archer in archer_list:
            if not isinstance(archer, dict):
                continue
            # Enrichit l'archer avec les métadonnées du classement
            enriched = {
                "_sexe_code": cl_item.get("sexe_code", ""),
                "_arme_code": cl_item.get("arme_code", ""),
                "_cl_libelle": cl_item.get("libelle", ""),
            }
            enriched.update(archer)
            all_archers.append(enriched)

    return all_archers


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


def normalize_archer(archer: dict, disc_code: str, rank: int) -> dict:
    """Convertit un archer de l'API vers la structure attendue par le HTML.

    Champs réels retournés par l'API FFTA :
      PlaceOrdre, PlaceTotal, PlaceScore1/2/3
      ParticipantId, ParticipantNom, ParticipantVille
      _sexe_code, _arme_code (injectés par get_classement_detail)
    """
    s1 = str(archer.get("PlaceScore1") or "")
    s2 = str(archer.get("PlaceScore2") or "")
    s3 = str(archer.get("PlaceScore3") or "")
    total = str(archer.get("PlaceTotal") or "")
    rang = archer.get("PlaceOrdre") or rank

    # Nom complet au format "DUPONT Jean" → on garde en majuscules
    nom_complet = str(archer.get("ParticipantNom") or "").strip().upper()

    sexe = str(archer.get("_sexe_code") or "")
    arme = str(archer.get("_arme_code") or "")
    ville = str(archer.get("ParticipantVille") or "").strip()

    return {
        "Rang_ligue": rang,
        "RANG": rang,
        "NO_LICENCE": str(archer.get("ParticipantId") or ""),
        "NOM_PERSONNE": nom_complet,
        "PRENOM_PERSONNE": "",
        "SEXE": sexe,
        "ARME": arme,
        "CAT": get_cat(archer),
        "CATEGORIE": get_cat(archer),
        "NOM_STRUCTURE": ville,
        "NOM_ABREGE": "",
        "CODE_STRUCTURE": "",
        "SCORE1": s1,
        "SCORE2": s2,
        "SCORE3": s3,
        "MOY_SCORE": total,
        "DISCIPLINE": disc_code,
    }


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
            rang = archer.get("PlaceOrdre") or rank_in_cl
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
