"""Report legacy credential status or atomically replace plaintext with a hash.

No credential plaintext is emitted. Migration retains the existing secret during
the compatibility window; operators must subsequently issue named replacements,
update agents, and revoke the migrated credential, particularly after log exposure.
"""

import argparse
import json
from uuid import uuid4
from src.services.auth import token_digest
from src.services.credentials import token_repository
from src.services.brain_identity import validate_brain_id
from src.services.data.main import data_adapter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--migrate", metavar="BRAIN_ID", help="Move one legacy PAT to the hash-only registry (PostgreSQL)")
    args = parser.parse_args()
    if not args.migrate:
        for brain in data_adapter.get_brains_list():
            print(json.dumps({"brain_id": brain.name_key, "legacy_plaintext_present": bool(brain.pat),
                              "rotation_required": bool(brain.pat)}))
        return
    from src.config import config
    if config.data_db != "postgresql":
        parser.error("Atomic legacy migration requires the PostgreSQL data backend")
    brain_id = validate_brain_id(args.migrate)
    token_repository.initialize()
    # Lock the source and migrate both representations in ONE system transaction.
    with token_repository.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id,pat FROM data_brains WHERE name_key=%s FOR UPDATE", (brain_id,))
            row = cur.fetchone()
            if row is None:
                parser.error("Brain not found")
            if row[1]:
                cur.execute("""INSERT INTO access_tokens
                    (id,label,brain_id,brain_resource_id,token_prefix,token_hash,permissions,legacy,created_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,TRUE,'legacy-migration')
                    ON CONFLICT (token_hash) DO NOTHING""",
                    (str(uuid4()), 'migrated-legacy', brain_id, row[0], 'legacy', token_digest(row[1]),
                     ['memory:read', 'memory:write', 'brain:read']))
                cur.execute("UPDATE data_brains SET pat='',document=document-'pat' WHERE name_key=%s", (brain_id,))
        conn.commit()
    print(json.dumps({"brain_id": brain_id, "status": "hash-only", "rotation_required": True}))


if __name__ == "__main__":
    main()
