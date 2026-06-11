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
# Si FFTA_SAISON est défini mais vide (cas du déclenchement automatique GitHub Actions),
# on retombe sur l'année courante pour éviter de passer SaisonAnnee="" à l'API.
_env_saison = os.environ.get("FFTA_SAISON", "")
SAISON = _env_saison.strip() if _env_saison.strip() else str(datetime.now().year)
OUTPUT_DIR = Path(os.environ.get("FFTA_OUTPUT_DIR", "data"))

# Ligue cible : CR12 = Pays de la Loire
LIGUE_CODE = "CR12"

# ─── Mapping Para : licence → CAT_CLASS / CAT_TIR ────────────────────────────
# L'API FFTA ne retourne pas la classification fonctionnelle individuelle (W1, B2, ST…)
# dans les données de classement. On utilise un fichier de mapping maintenu manuellement
# depuis les CSV FFTA (para_class.json). Le fichier est versionné dans data/.
_PARA_CLASS_FILE = OUTPUT_DIR / "para_class.json"

def _load_para_class_map() -> dict:
    """Charge le mapping licence → {CAT_CLASS, CAT_TIR} depuis data/para_class.json."""
    if _PARA_CLASS_FILE.exists():
        try:
            with open(_PARA_CLASS_FILE, encoding="utf-8") as f:
                data = json.load(f)
                return data.get("map", data) if isinstance(data, dict) and "map" in data else data
        except Exception:
            pass
    return {}

PARA_CLASS_MAP: dict = {}  # chargé dans run() après création du dossier output

# Nombre maximum d'archers conservés par classement si le filtre Ligue
# ne fonctionne pas côté serveur (filet de sécurité anti-crash navigateur).
MAX_ARCHERS_PER_CLASSEMENT = int(os.environ.get("FFTA_MAX_ARCHERS", "200"))

# Disciplines à récupérer : code API → nom de fichier JSON de sortie
DISCIPLINES = {
    "S": "Tir 18m.json",       # Tir en Salle 18m
    # T est géré spécialement : deux fichiers TAE I.json + TAE N.json
    # (séparation par distance : ≥50m = International, <50m = National)
    "C": "Campagne.json",       # Campagne
    "N": "Nature.json",         # Nature
    "3": "3D.json",             # 3D
    "H": "para ext.json",       # Para-tir extérieur
    "I": "Para salle 18m.json", # Para-tir 18m
}

# Classements équipes : discipline → fichier JSON de sortie
EQUIPE_DISCIPLINES = {
    "S": "equipes_S.json",
    "T": "equipes_T.json",
    "C": "equipes_C.json",
    "N": "equipes_N.json",
    "3": "equipes_3.json",
}

# Mots-clés positifs pour détecter un libellé de classement équipe
_TEAM_KW = ("double mixte", "equipe", "équipe", "jeune mixte", "mixte jeune",
            "parcours nature", "tir sur cibles")

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


def get_token(session: requests.Session, namespace: str = "Classements") -> str:
    """Obtient un token valide 1h pour le namespace donné."""
    url = f"{BASE_URL}/{namespace}/GetToken"
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
            log.info("Token %s obtenu ✓", namespace)
            return token
        raise RuntimeError(f"Réponse OK mais token absent : {data}")
    except Exception as e:
        raise RuntimeError(
            f"Impossible d'obtenir un token FFTA ({namespace}).\n"
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
            # distance est une liste ex: ["70m - 122cm"] → on prend le premier élément
            dist_raw = cl_item.get("distance") or []
            dist_str = dist_raw[0] if isinstance(dist_raw, list) and dist_raw else str(dist_raw or "")
            enriched = {
                "_sexe_code": cl_item.get("sexe_code", ""),
                "_arme_code": cl_item.get("arme_code", ""),
                "_cl_libelle": cl_item.get("libelle", ""),
                "_distance_raw": dist_str,
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

    # Distance et blason depuis le classement (ex: "70m - 122cm")
    dist_raw = str(archer.get("_distance_raw") or "")
    if " - " in dist_raw:
        dist_parts = dist_raw.split(" - ", 1)
        distance = dist_parts[0].strip()   # ex: "70m"
        blason   = dist_parts[1].strip()   # ex: "122cm"
    else:
        distance = dist_raw
        blason   = ""

    # Type TAE : déterminé par la taille du blason (fourni par l'appelant via _tae_type)
    # 122cm = International (I), 80cm = National (N)
    tae_type = str(archer.get("_tae_type") or "")

    # ── Champs Para (disciplines H et I) ─────────────────────────────────────
    # L'API FFTA ne retourne pas la classification fonctionnelle individuelle.
    # On la récupère depuis PARA_CLASS_MAP (data/para_class.json).
    # CAT_TIR  : groupe du classement (W1, HV 2-3, OPEN, FEDERAL…) — dérivé du libellé
    # CAT_CLASS: classification individuelle (W1, B2, ST, W2, NEI, SOURD…) — depuis le mapping
    cat_tir = ""
    cat_class = ""
    if disc_code in ("H", "I"):
        cl_libelle = str(archer.get("_cl_libelle") or "")
        # Extrait le préfixe avant "Scratch" ou "Jeunes" pour CAT_TIR
        m = _re.match(r'^(.*?)\s+(?:Scratch|Jeunes)\b', cl_libelle, _re.IGNORECASE)
        cat_tir = m.group(1).strip() if m else (cl_libelle.split()[0] if cl_libelle else "")
        # CAT_CLASS depuis le mapping licence
        lic_key = str(archer.get("LicenceCodeAdherent") or archer.get("ParticipantId") or "")
        para_info = PARA_CLASS_MAP.get(lic_key, {})
        cat_class = para_info.get("CAT_CLASS", "")
        # Si le mapping a un CAT_TIR plus précis, on le préfère
        if para_info.get("CAT_TIR"):
            cat_tir = para_info["CAT_TIR"]

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
        "DISTANCE": distance,
        "BLASON": blason,
        "TAE_TYPE": tae_type,
        "CAT_TIR": cat_tir,
        "CAT_CLASS": cat_class,
    }


# ─── Logique principale ────────────────────────────────────────────────────────

import re as _re

def tae_type_from_classement(arme_code: str, cat_codes: set, distances: list) -> str:
    """Détermine si un classement TAE est International (I) ou National (N)
    selon les règles officielles FFTA (arme + catégorie + distance + blason).

    Arc Classique (C) :
      U11              → 20m-80cm  = I
      U13              → 30m-80cm  = I
      U15              → 40m-80cm  = I
      U18              → 60m-122cm = I
      U21, S1, S2      → 70m-122cm = I
      S3               → 60m-122cm = I

    Arc à Poulies (P) :
      U18, U21, S1, S2, S3 → 50m-80cm = I

    Tout le reste → N
    """
    # Parse toutes les paires (mètres, blason_cm) présentes dans le classement
    dist_pairs = []
    for d in distances:
        m = _re.match(r'(\d+)m\s*-\s*(\d+)cm', str(d))
        if m:
            dist_pairs.append((int(m.group(1)), int(m.group(2))))

    if not dist_pairs:
        return "N"

    # Pour les classements multi-distances (ex: U18/U21 avec 70m + 60m),
    # on vérifie si AU MOINS UNE distance correspond à un standard TAE I.
    for metres, blason in dist_pairs:
        if arme_code == "C":  # Arc Classique
            if "U11" in cat_codes and metres == 20 and blason == 80:
                return "I"
            if "U13" in cat_codes and metres == 30 and blason == 80:
                return "I"
            if "U15" in cat_codes and metres == 40 and blason == 80:
                return "I"
            if "U18" in cat_codes and metres == 60 and blason == 122:
                return "I"
            if cat_codes & {"U21", "S1", "S2"} and metres == 70 and blason == 122:
                return "I"
            if "S3" in cat_codes and metres == 60 and blason == 122:
                return "I"
        elif arme_code == "P":  # Arc à Poulies
            if cat_codes & {"U18", "U21", "S1", "S2", "S3"} and metres == 50 and blason == 80:
                return "I"

    return "N"


def get_tae_classements_map(session, token) -> dict[str, dict]:
    """Appelle GetClassements pour TAE et retourne un dict
    {classement_id: {"libelle": ..., "tae_type": "I"/"N", "distance": [...], ...}}
    """
    data = api_get(session, "Classements/Classements", token,
                   SaisonAnnee=SAISON, DisciplineCode="T")
    response = data.get("Response", {})
    classements = response.get("ClassementsArray") or []
    if isinstance(response, list):
        classements = response

    result = {}
    for cl in classements:
        if not isinstance(cl, dict):
            continue
        cl_id = str(cl.get("id", ""))
        if not cl_id:
            continue
        distances = cl.get("distance") or []
        if not isinstance(distances, list):
            distances = [distances] if distances else []
        arme_code = cl.get("arme_code", "")
        cat_codes = set((cl.get("categories_age") or {}).keys())
        tae_type = tae_type_from_classement(arme_code, cat_codes, distances)
        result[cl_id] = {
            "libelle": cl.get("libelle", ""),
            "tae_type": tae_type,
            "distance": distances,
        }

    log.info("  GetClassements TAE → %d classements trouvés (%d I, %d N, %d inconnus)",
             len(result),
             sum(1 for v in result.values() if v["tae_type"] == "I"),
             sum(1 for v in result.values() if v["tae_type"] == "N"),
             sum(1 for v in result.values() if v["tae_type"] == ""))
    return result


def _is_team_libelle(libelle: str) -> bool:
    """Retourne True si le libellé correspond à un classement équipe (pas individuel)."""
    l = (libelle or "").strip().lower()
    for kw in _TEAM_KW:
        if kw in l:
            return True
    if _re.search(r'\bu18/u21\b', l):
        return True
    # "Arc Classique/Poulies/Nu Homme/Femme XXXX" sans préfixe Senior/Adulte/U
    if _re.match(r'^arc\s+(classique|[àa]\s*poulies|nu)\s+(homme|femme)\b', l):
        return True
    # Marqueurs négatifs → individuel
    if _re.match(r'^(senior|adulte|scratch|u\d+\s)', l):
        return False
    return False


def _extract_division_from_libelle(libelle: str) -> str:
    """Extrait le code division (D1/D2/DR) depuis le libellé d'un classement."""
    import re
    lib = libelle.upper()
    if re.search(r'\bD1\b', lib):
        return "D1"
    if re.search(r'\bD2\b', lib):
        return "D2"
    if re.search(r'\bDR\b', lib) or "DIV" in lib and "REG" in lib:
        return "DR"
    return ""


def normalize_team(team: dict, disc_code: str, cl_libelle: str, rang_ligue: int,
                   sexe_code: str, arme_code: str) -> dict:
    """Normalise une entrée équipe depuis l'API FFTA."""
    nom_structure = str(team.get("StructureNom") or "")
    nom_abrege = str(
        team.get("StructureNomCourt") or team.get("NomAbrege") or nom_structure[:25]
    )
    code_structure = str(team.get("StructureCode") or "")
    ville = str(
        team.get("AdresseVilleSiege") or team.get("StructureVilleSiege") or
        team.get("Ville") or ""
    )
    rang_nat = str(team.get("PlaceOrdre") or "")
    rang_ligue_raw = str(team.get("PlaceLigue") or rang_ligue)
    division = str(
        team.get("PlaceDivision") or team.get("DivisionCode") or
        team.get("Division") or ""
    ) or _extract_division_from_libelle(cl_libelle)
    pre_inscrit = str(team.get("PreInscrit") or team.get("PreInscription") or "")
    quota = str(team.get("Quota") or "")
    s1 = str(team.get("PlaceScore1") or "0")
    s2 = str(team.get("PlaceScore2") or "0")
    s3 = str(team.get("PlaceScore3") or "0")
    total = str(team.get("PlaceTotal") or team.get("PlaceMoyenne") or "0")
    dept = dept_from_club_code(code_structure)
    return {
        "RANG": rang_nat or str(rang_ligue),
        "RANG_LIGUE": rang_ligue_raw,
        "DIVISION": division,
        "PRE_INSCRIT": pre_inscrit or division,
        "QUOTA": quota,
        "NOM_ABREGE": nom_abrege,
        "NOM_STRUCTURE": nom_structure,
        "SEXE_EQUIPE": sexe_code,
        "ARME_EQUIPE": arme_code,
        "CATEGORIE_CLASSEMENT": cl_libelle,
        "CODE_STRUCTURE": code_structure,
        "VILLE": ville,
        "DEPARTEMENT": dept,
        "SCORE1": s1,
        "SCORE2": s2,
        "SCORE3": s3,
        "MOY_SCORE": total,
        "_classement_nom": cl_libelle,
        "DISCIPLINE": disc_code,
    }


def fetch_teams_disc(session: requests.Session, token: str, disc_code: str) -> list[dict]:
    """Récupère les classements équipes PDL (CR12) pour une discipline."""
    log.info("→ Équipes discipline %s", disc_code)
    PDL_DEPTS = {"44", "49", "53", "72", "85"}

    # Liste complète des classements pour cette discipline
    all_cls = get_classements_list(session, token, disc_code)

    # Filtre : classements équipe uniquement
    team_cls = []
    for cl in all_cls:
        if not isinstance(cl, dict):
            continue
        cl_type = str(cl.get("type") or cl.get("typeClassement") or "").lower()
        if cl_type in ("equipe", "équipe", "team"):
            team_cls.append(cl)
        elif cl_type in ("individuel", "individual"):
            continue
        elif _is_team_libelle(str(cl.get("libelle") or "")):
            team_cls.append(cl)

    log.info("  %d classements équipe trouvés sur %d total", len(team_cls), len(all_cls))
    if not team_cls:
        log.warning("  Aucun classement équipe pour discipline %s", disc_code)
        return []

    all_rows: list[dict] = []
    first_logged = False

    for cl in team_cls:
        cl_id = str(cl.get("id") or "")
        cl_libelle = str(cl.get("libelle") or cl_id)
        sexe_code = str(cl.get("sexe_code") or "X")
        arme_code = str(cl.get("arme_code") or "")
        if not cl_id:
            continue

        try:
            data = api_get(session, "Classements/Classement", token, Classement=cl_id)
        except Exception as e:
            log.warning("  Erreur GetClassement équipe (%s – %s): %s", cl_id, cl_libelle, e)
            continue

        response = data.get("Response", {})
        classement_array = response.get("ClassementArray") or []
        if isinstance(response, list):
            classement_array = response

        cl_rows: list[dict] = []
        for cl_item in classement_array:
            if not isinstance(cl_item, dict):
                continue
            raw = cl_item.get("archers") or cl_item.get("equipes") or []
            if isinstance(raw, dict):
                entry_list: list = sorted(
                    raw.values(), key=lambda x: int(x.get("PlaceOrdre") or 9999)
                )
            elif isinstance(raw, list):
                entry_list = raw
            else:
                entry_list = []

            item_sexe = str(cl_item.get("sexe_code") or sexe_code)
            item_arme = str(cl_item.get("arme_code") or arme_code)

            rang_ligue = 0
            for team in entry_list:
                if not isinstance(team, dict):
                    continue
                # Log structure première équipe (debug)
                if not first_logged:
                    log.info("  DEBUG structure première équipe (%s): clés=%s",
                             cl_libelle, list(team.keys()))
                    first_logged = True
                # Filtre PDL : StructureCodeRegion prioritaire, sinon département
                if team.get("StructureCodeRegion", "") == LIGUE_CODE:
                    pass
                else:
                    dept = dept_from_club_code(str(team.get("StructureCode") or ""))
                    if dept not in PDL_DEPTS:
                        continue
                rang_ligue += 1
                row = normalize_team(team, disc_code, cl_libelle, rang_ligue, item_sexe, item_arme)
                cl_rows.append(row)

        log.info("  %-50s → %d équipes PDL", cl_libelle[:50], len(cl_rows))
        all_rows.extend(cl_rows)
        time.sleep(0.2)

    return all_rows


def get_disc_ids_dynamic(session, token, disc_code) -> list[str]:
    """Découverte dynamique des IDs de classements via l'API (comme TAE).
    Retourne tous les IDs de classements individuels pour une discipline.
    """
    data = api_get(session, "Classements/Classements", token,
                   SaisonAnnee=SAISON, DisciplineCode=disc_code)
    response = data.get("Response", {})
    classements = response.get("ClassementsArray") or response.get("Classements") or []
    if isinstance(response, list):
        classements = response
    ids = []
    for cl in classements:
        if not isinstance(cl, dict):
            continue
        cl_id = str(cl.get("id") or cl.get("ClassementId") or cl.get("Id") or "")
        libelle = (cl.get("libelle") or "").strip()
        # Exclure les classements équipes
        if cl_id and not _is_team_libelle(libelle):
            ids.append(cl_id)
    log.info("  Découverte dynamique %s → %d classements individuels", disc_code, len(ids))
    return ids


def fetch_discipline(session: requests.Session, token: str, disc_code: str,
                     tae_map=None) -> list[dict]:
    """Récupère les archers PDL (CR12) pour une discipline.

    Pour TAE (disc_code='T'), utilise tae_map (issu de GetClassements).
    Pour les autres disciplines, combine IDs hardcodés + découverte dynamique
    pour s'assurer de ne pas rater de nouveaux classements publiés en cours de saison.
    """
    log.info("→ Discipline %s  (saison %s, ligue %s)", disc_code, SAISON, LIGUE_CODE)

    if disc_code == "T" and tae_map:
        cl_ids = list(tae_map.keys())
    else:
        # Découverte dynamique des IDs courants
        dynamic_ids = get_disc_ids_dynamic(session, token, disc_code)
        # IDs hardcodés en fallback (au cas où l'API ne renvoit rien)
        hardcoded_ids = CLASSEMENT_IDS_BY_DISC.get(disc_code, [])
        # Union : dynamiques en premier, hardcodés en complément
        seen = set(dynamic_ids)
        extra = [i for i in hardcoded_ids if i not in seen]
        if extra:
            log.info("  + %d IDs hardcodés non présents dans la découverte dynamique", len(extra))
        cl_ids = dynamic_ids + extra

    if not cl_ids:
        log.warning("  Aucun ID de classement trouvé pour %s", disc_code)
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

        # Libellé et type TAE depuis la map API (ou depuis l'archer enrichi)
        if disc_code == "T" and tae_map and cl_id in tae_map:
            cl_meta = tae_map[cl_id]
            cl_name = cl_meta["libelle"] or archers[0].get("_cl_libelle") or cl_id
            tae_type = cl_meta["tae_type"]
        else:
            cl_name = archers[0].get("_cl_libelle") or cl_id
            tae_type = ""

        log.info("  %-60s  %3d archers  [TAE:%s]", str(cl_name)[:60], len(archers), tae_type or "-")

        for rang_pdl, archer in enumerate(archers, start=1):
            rang_nat = str(archer.get("PlaceOrdre") or "")
            # Injecte _tae_type pour que normalize_archer puisse l'utiliser
            archer["_tae_type"] = tae_type
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

    # Charge le mapping Para (classification individuelle)
    global PARA_CLASS_MAP
    PARA_CLASS_MAP = _load_para_class_map()
    log.info("Mapping Para chargé : %d archers", len(PARA_CLASS_MAP))

    session = requests.Session()
    session.headers["Accept"] = "application/json"

    log.info("Obtention du token FFTA…")
    token = get_token(session, "Classements")
    token_resultats = get_token(session, "Resultats")

    results: dict[str, list] = {}

    for disc_code, filename in DISCIPLINES.items():
        rows = fetch_discipline(session, token, disc_code)
        results[disc_code] = rows

        out_path = OUTPUT_DIR / filename
        payload = {"meta": meta, "rows": rows}
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("  ✓ %s  (%d lignes)", out_path, len(rows))

    # Traitement spécial TAE : on interroge d'abord GetClassements pour obtenir
    # la liste dynamique avec la taille du blason (122cm=I, 80cm=N)
    log.info("→ TAE : récupération de la liste des classements via l'API…")
    tae_map = get_tae_classements_map(session, token)
    tae_rows = fetch_discipline(session, token, "T", tae_map=tae_map)

    tae_i = [r for r in tae_rows if r.get("TAE_TYPE") == "I"]
    tae_n = [r for r in tae_rows if r.get("TAE_TYPE") == "N"]
    for filename, subset in [("TAE I.json", tae_i), ("TAE N.json", tae_n)]:
        out_path = OUTPUT_DIR / filename
        payload = {"meta": meta, "rows": subset}
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("  ✓ %s  (%d lignes)", out_path, len(subset))

    # ── Classements équipes ───────────────────────────────────────────────────
    log.info("→ Récupération des classements équipes…")
    for disc_code, filename in EQUIPE_DISCIPLINES.items():
        equipe_rows = fetch_teams_disc(session, token, disc_code)
        out_path = OUTPUT_DIR / filename
        payload = {"meta": meta, "rows": equipe_rows}
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("  ✓ %s  (%d équipes)", out_path, len(equipe_rows))

    # ── Palmarès individuels ──────────────────────────────────────────────────
    # Collecte toutes les licences PDL uniques à travers tous les classements
    all_rows_all_discs = []
    for rows in results.values():
        all_rows_all_discs.extend(rows)
    all_rows_all_discs.extend(tae_rows)

    licences = sorted({
        str(r.get("NO_LICENCE", "")).strip()
        for r in all_rows_all_discs
        if r.get("NO_LICENCE")
    })
    log.info("→ Palmarès : %d licences PDL uniques à récupérer…", len(licences))

    palmares_dir = OUTPUT_DIR / "palmares"
    palmares_dir.mkdir(parents=True, exist_ok=True)

    ok, errors = 0, 0
    for i, lic in enumerate(licences, 1):
        try:
            data = api_get(session, "Resultats/ResultatsParArcher", token_resultats,
                           NumeroLicence=lic, SaisonAnnee=SAISON)
            out_path = palmares_dir / f"{lic}.json"
            out_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            ok += 1
            if i % 50 == 0:
                log.info("  … %d/%d palmarès récupérés", i, len(licences))
        except Exception as e:
            log.warning("  Palmares(%s) : %s", lic, e)
            errors += 1
        time.sleep(0.15)   # respecte le rate-limit FFTA

    log.info("  ✓ Palmarès : %d OK, %d erreurs — dossier %s/", ok, errors, palmares_dir)

    # Fichier méta global (date de mise à jour)
    meta_path = OUTPUT_DIR / "meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Terminé. Fichiers écrits dans %s/", OUTPUT_DIR)


if __name__ == "__main__":
    run()
