"""The worker's own small database: which events each consumer has handled.

Lives in the `worker` schema under the `worker` role, which can see nothing
in `commerce` (README section 12: "limited writes, processed-events").
"""

from psycopg import sql


def ensure_schema(db, schema):
    ident = sql.Identifier(schema)
    exists = db.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", [schema]).fetchone()
    if not exists:  # db/init creates it in Compose; tests create their own
        db.execute(sql.SQL("CREATE SCHEMA {}").format(ident))
    db.execute(sql.SQL("""
        CREATE TABLE IF NOT EXISTS {}.processed_event (
            consumer     text        NOT NULL,
            event_id     uuid        NOT NULL,
            outcome      text        NOT NULL DEFAULT 'processing',
            processed_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (consumer, event_id)
        )""").format(ident))
    db.commit()


def run_once(db, schema, consumer, event_id, fn):
    """Run `fn` at most once per (consumer, event id).

    The marker row and the work share a transaction: if `fn` raises, the row
    rolls back and a retry runs it again. A concurrent duplicate blocks on the
    primary key until the first finishes, then sees the conflict and skips.
    Side effects outside the database (an e-mail) can still repeat if the
    process dies after sending but before commit: at-least-once, by design.
    """
    table = sql.Identifier(schema, "processed_event")
    with db.transaction():
        inserted = db.execute(
            sql.SQL("INSERT INTO {} (consumer, event_id) VALUES (%s, %s) ON CONFLICT DO NOTHING").format(table),
            [consumer, event_id],
        ).rowcount
        if not inserted:
            return "duplicate"
        outcome = fn() or "done"
        db.execute(sql.SQL("UPDATE {} SET outcome = %s WHERE consumer = %s AND event_id = %s").format(table),
                   [outcome, consumer, event_id])
    return outcome


def purge(db, schema, days=30):
    table = sql.Identifier(schema, "processed_event")
    with db.transaction():
        return db.execute(sql.SQL("DELETE FROM {} WHERE processed_at < now() - make_interval(days => %s)")
                          .format(table), [days]).rowcount
