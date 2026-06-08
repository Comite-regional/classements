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

# Nombre maximum d'archers conservés par classement si le filtre Ligue
# ne fonctionne pas côté serveur (filet de sécurité anti-crash navigateur).
MAX_ARCHERS_PER_CLASSEMENT = int(os.environ.get("FFTA_MAX_ARCHERS", "200"))

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

# IDs de classements nationaux FFTA 2026 extraits du PDF officiel.
# On les appelle directement (sans passer par GetClassements) en ajoutant
# Ligue=CR12 pour récupérer uniquement les archers Pays de la Loire.
CLASSEMENT_IDS_BY_DISC: dict[str, list[str]] = {
    "S": [  # Tir à 18m — 40 classements
        "14023","14024","14025","14026","14027","14028",
        "14080","14081","14082","14083","14084","14085","14086","14087","14088",
        "14089","14090","14091","14092","14093","14094","14095","14096","14097","14098",
        "14099","14100","14101","14102","14103","14104","14105","14106","14107","14108",
        "14109","14110","14111","14112","14113",
    ],
    "T": [  # TAE — 68 classements
        "13951","13952","13953","13954","13955","13956","13957","13958","13959","13960",
        "13961","13962","13963","13964","13965","13966","13967","13968","13969","13970",
        "13971","13972","13973","13974","13975","13976","13977","13978","13979","13980",
        "13981","13982","13983","13984","13985","13986","13987","13988","13989","13990",
        "13991","13992","13993","13994","13995","13996","13997","13998","13999","14000",
        "14001","14002","14003","14004","14005","14006","14007","14008","14009","14010",
        "14011","14012","14013","14014","14017","14018","14019","14020",
    ],
    "C": [  # Campagne — 50 classements
        "14029","14030","14031","14032","14033","14034",
        "14208","14209","14210","14211","14212","14213","14214","14215","14216",
        "14217","14218","14219","14220","14221","14222","14223","14224","14225","14226",
        "14227","14228","14229","14230","14231","14232","14233","14234","14235","14236",
        "14237","14238","14239","14240","14241","14242","14243","14244","14245","14246",
        "14247","14248","14249","14250","14251",
    ],
    "N": [  # Nature — 67 classements
        "14252","14253","14254","14255","14256","14257","14258","14259","14260","14261",
        "14264","14265","14266","14267","14268","14269","14270","14271","14272","14273",
        "14274","14275","14276","14277","14278","14279","14280","14281","14282","14283",
        "14284","14285","14286","14287","14288","14289","14290","14291","14292","14293",
        "14294","14295","14296","14297","14298","14299","14300","14301","14302","14303",
        "14304","14305","14306","14307","14308","14309","14310","14311","14312","14313",
        "14314","14315","14316","14317","14318","14319","14320",
    ],
    "3": [  # 3D — 72 classements
        "14114","14115","14116","14117","14118","14119","14120","14121","14122","14123",
        "14124","14125","14126","14127","14128","14129","14130","14131","14132","14133",
        "14134","14135","14136","14137","14138","14139","14140","14141","14142","14143",
        "14144","14145","14146","14147","14148","14149","14150","14151","14152","14153",
        "14154","14155","14156","14157","14158","14159","14160","14161","14162","14163",
        "14164","14165","14166","14167","14168","14169","14170","14171","14172","14173",
        "14174","14175","14176","14177","14321","14322","14323","14324","14325","14326",
        "14327","14328",
    ],
    "H": [  # Para-tir extérieur — 36 classements
        "14329","14330","14331","14332","14333","14334","14335","14336","14337","14338",
        "14339","14340","14341","14342","14343","14344","14345","14346","14347","14348",
        "14349","14350","14351","14352","14353","14354","14355","14356","14357","14358",
        "14359","14360","14361","14362","14363","14364",
    ],
    "I": [  # Para-tir 18m — 23 classements
        "14365","14366","14367","14368","14369","14370","14371","14372","14373","14374",
        "14375","14376","14377","14378","14379","14380","14381","14382","14383","14384",
        "14385","14386","14387",
    ],
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
        if isinstance(raw_archers, dict):
            archer_list = list(raw_archers.values())
        elif isinstance(raw_archers, list):
            archer_list = raw_archers
        else:
            archer_list = []

        def sort_key(a):
            try:
                return int(a.get("PlaceOrdre") or 9999)
            except (ValueError, TypeError):
                return 9999

        archer_list.sort(key=sort_key)

        for archer in archer_list:
            if not isinstance(archer, dict):
                continue
            # Filtrage région : on ne garde que les archers PDL (CR12)
            if archer.get("StructureCodeRegion", "") != LIGUE_CODE:
                continue
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
    """Convertit un archer de l'API vers la structure attendue par le HTML."""
    s1 = str(archer.get("PlaceScore1") or "")
    s2 = str(archer.get("PlaceScore2") or "")
    s3 = str(archer.get("PlaceScore3") or "")
    total = str(archer.get("PlaceTotal") or "")
    rang = rank  # rang PDL (position dans la liste filtrée CR12)

    nom = str(archer.get("PersonneNom") or "").strip().upper()
    prenom = str(archer.get("PersonnePrenom") or "").strip().capitalize()
    licence = str(archer.get("LicenceCodeAdherent") or archer.get("ParticipantId") or "")
    club = str(archer.get("StructureNom") or archer.get("StructureNomCourt") or archer.get("ParticipantVille") or "")
    club_court = str(archer.get("StructureNomCourt") or "")
    code_structure = str(archer.get("StructureCode") or "")
    cat = str(archer.get("ParticipantCatAge") or archer.get("CategorieAgeCodeGroupe") or "")
    sexe = str(archer.get("CategorieAgeSexe") or archer.get("_sexe_code") or "")
    arme = str(archer.get("_arme_code") or "")

    # Département depuis StructureCodeDepartement (ex: "44000" → "44")
    dept_raw = str(archer.get("StructureCodeDepartement") or "")
    dept = dept_raw[:2] if dept_raw and dept_raw != "0" else ""

    return {
        "Rang_ligue": rang,
        "RANG": rang,
        "NO_LICENCE": licence,
        "NOM_PERSONNE": nom,
        "PRENOM_PERSONNE": prenom,
        "SEXE": sexe,
        "ARME": arme,
        "CAT": cat,
        "CATEGORIE": cat,
        "NOM_STRUCTURE": club,
        "NOM_ABREGE": club_court,
        "CODE_STRUCTURE": code_structure,
        "DEPARTEMENT": dept,
        "SCORE1": s1,
        "SCORE2": s2,
        "SCORE3": s3,
        "MOY_SCORE": total,
        "DISCIPLINE": disc_code,
    }


# ─── Logique principale ────────────────────────────────────────────────────────

def fetch_discipline(session: requests.Session, token: str, disc_code: str) -> list[dict]:
    """Récupère les archers PDL (CR12) pour une discipline.

    Utilise la liste d'IDs hardcodée (extraite du PDF officiel) pour
    appeler directement GetClassement avec Ligue=CR12, ce qui évite
    l'étape GetClassements et filtre les archers côté serveur FFTA.
    """
    log.info("→ Discipline %s  (saison %s, ligue %s)", disc_code, SAISON, LIGUE_CODE)

    cl_ids = CLASSEMENT_IDS_BY_DISC.get(disc_code, [])
    if not cl_ids:
        log.warning("  Aucun ID de classement configuré pour %s", disc_code)
        return []

    log.info("  %d classement(s) à interroger", len(cl_ids))
    all_rows: list[dict] = []

    for cl_id in cl_ids:
        try:
            archers = get_classement_detail(session, token, cl_id)
        except Exception as e:
            log.warning("  Erreur GetClassement(%s): %s", cl_id, e)
            continue

        if not archers:
            log.debug("  Classement %s : 0 archer (vide ou hors PDL)", cl_id)
            continue

        # Le libellé vient du premier archer (enrichi par get_classement_detail)
        cl_name = archers[0].get("_cl_libelle") or cl_id
        log.info("  %-60s  %3d archers", str(cl_name)[:60], len(archers))

        for rang_pdl, archer in enumerate(archers, start=1):
            rang_nat = str(archer.get("PlaceOrdre") or "")
            row = normalize_archer(archer, disc_code, rang_pdl)
            row["RANG_NAT"] = rang_nat
            row["_classement_nom"] = str(cl_name)
            all_rows.append(row)

        time.sleep(0.2)

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
