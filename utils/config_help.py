
SITE_ROOT = os.path.realpath(os.path.dirname(__file__))
SECRETS_URL = os.path.join(SITE_ROOT, "secrets.json")

with open(SECRETS_URL, encoding="utf-8") as f:
    SECRETS = json.loads(f.read())