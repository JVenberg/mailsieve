"""Cloud Run entrypoint. Both routes require an OIDC token (Cloud Run IAM), not a public URL.

POST /push   Pub/Sub push from Gmail's watch(): classify new inbox mail now.
POST /sweep  Cloud Scheduler, hourly: catch up on missed mail, apply delayed actions, renew the watch.
"""

import os

from flask import Flask

from . import config, engine, gmail

app = Flask(__name__)
CFG = config.load()
DRY_RUN = os.environ.get("DRY_RUN") == "1"


@app.post("/push")
def push():
    engine.classify(gmail.service(), CFG, dry_run=DRY_RUN)
    return "", 204


@app.post("/sweep")
def sweep():
    svc = gmail.service()
    engine.classify(svc, CFG, dry_run=DRY_RUN)
    engine.sweep(svc, CFG, dry_run=DRY_RUN)
    if os.environ.get("WATCH_TOPIC"):
        engine.watch(svc)
    return "", 204
