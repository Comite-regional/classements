#!/usr/bin/env python3
"""
Affiche la structure complète d'une entrée équipe depuis l'API FFTA.
Utile pour trouver le champ Division (D1/D2/DR) dans la réponse.

Usage:
  export FFTA_SESSION_IDENTITE='ton_code'
  python debug_teams_structure.py
"""

import os, json
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    import requests
except ImportError:
    raise SystemExit("pip install requests")

SESSION_IDENTITE = os.environ.get("FFTA_SESSION_IDENTITE", "")
BASE_URL = "https://extranet.ffta.fr/ws/rest"
SAISON = os.environ.get("FFTA_SAISON", str(datetime.now().year))

if not SESSION_IDENTITE:
    raise SystemExit("Définis FFTA_SESSION_IDENTITE !\nexport FFTA_SESSION_IDENTITE='ton_code'")

def make_password():
    return datetime.now(ZoneInfo("Europe/Paris")).strftime("%Y%m%d%H%M")

session = requests.Session()
session.headers["Accept"] = "application/json"

print("Connexion à l'API FFTA...")
resp = session.get(f"{BASE_URL}/Classements/GetToken", params={
    "sessionIdentite": SESSION_IDENTITE,
    "password": make_password(),
    "format": "json",
}, timeout=30)
resp.raise_for_status()
data = resp.json()
token = (data.get("Response", {}).get("token")
      or data.get("Response", {}).get("Token")
      or data.get("token") or data.get("Token"))
print(f"Token OK ✓\n")

# Classements équipe TAE connus (Arc Classique Homme, Arc à Poulies Homme...)
TEAM_CL_IDS = ["13948", "13949", "13946", "13947", "13942", "13945", "13944", "13950"]

for cl_id in TEAM_CL_IDS:
    print(f"\n{'='*70}")
    print(f"Classement ID: {cl_id}")
    resp = session.get(f"{BASE_URL}/Classements/Classement", params={
        "token": token,
        "Classement": cl_id,
    }, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    response = data.get("Response", {})
    classement_array = response.get("ClassementArray") or []
    if isinstance(response, list):
        classement_array = response

    for cl_item in classement_array:
        if not isinstance(cl_item, dict):
            continue
        # Métadonnées du classement (sans equipes/archers)
        meta = {k: v for k, v in cl_item.items() if k not in ("archers", "equipes")}
        print(f"\nMéta classement item:\n{json.dumps(meta, ensure_ascii=False, indent=2)}")

        # Récupère la liste des équipes
        entries = cl_item.get("equipes") or cl_item.get("archers") or []
        if isinstance(entries, dict):
            entries = list(entries.values())

        if entries:
            print(f"\nNombre d'équipes/archers: {len(entries)}")
            print(f"\n--- Première entrée (TOUTES les clés) ---")
            print(json.dumps(entries[0], ensure_ascii=False, indent=2))

            # Toutes les clés présentes dans toutes les entrées
            all_keys = set()
            for e in entries:
                if isinstance(e, dict):
                    all_keys.update(e.keys())
            print(f"\nToutes les clés disponibles: {sorted(all_keys)}")

            # Valeurs du champ Division/DivisionCode si présent
            for key in sorted(all_keys):
                if "div" in key.lower() or "division" in key.lower():
                    vals = [str(e.get(key, "")) for e in entries if isinstance(e, dict)]
                    print(f"\nChamp '{key}': {vals[:10]}")
        break  # Un seul cl_item par classement
