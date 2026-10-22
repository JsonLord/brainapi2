"""Track committed content writes across all shared PostgreSQL client pools.

Only a boolean is retained; query text and parameters are never stored/logged.
Periodic semantic reconciliation also covers administrative connections and DDL.
"""
import re
from psycopg2.extensions import connection, cursor
from .state import mark_dirty

_FACTORIES = {}
_WRITES = {"INSERT", "UPDATE", "DELETE", "TRUNCATE", "COPY", "CREATE", "ALTER", "DROP"}


class _TrackingCursor:
    def _note(self, query, *, copy=False):
        command = (self.statusmessage or "").split(" ", 1)[0]
        if not copy and command not in _WRITES:
            return
        conn = self.connection
        if conn.info.dbname == conn.backup_system_database:
            if hasattr(query, "as_string"):
                query = query.as_string(conn)
            if isinstance(query, bytes):
                query = query.decode("utf-8", errors="replace")
            if not re.search(r"\b(?:data_brains|data_workspaces)\b", str(query), re.I):
                return
        if conn.autocommit:
            mark_dirty()
        else:
            conn._backup_dirty = True

    def execute(self, query, vars=None):
        result = super().execute(query, vars)
        self._note(query)
        return result

    def executemany(self, query, vars_list):
        result = super().executemany(query, vars_list)
        self._note(query)
        return result

    def copy_expert(self, sql, file, size=8192):
        result = super().copy_expert(sql, file, size)
        if re.search(r"\bFROM\s+STDIN\b", str(sql), re.I):
            self._note(sql, copy=True)
        return result

    def copy_from(self, file, table, sep="\t", null="\\N", size=8192, columns=None):
        result = super().copy_from(file, table, sep, null, size, columns)
        self._note(table, copy=True)
        return result


class DirtyConnection(connection):
    def cursor(self, name=None, cursor_factory=None, scrollable=None, withhold=False):
        base = cursor_factory or self.cursor_factory or cursor
        factory = _FACTORIES.get(base)
        if factory is None:
            factory = _FACTORIES.setdefault(base, type(f"Content{base.__name__}", (_TrackingCursor, base), {}))
        return super().cursor(name=name, cursor_factory=factory, scrollable=scrollable, withhold=withhold)

    def commit(self):
        changed = getattr(self, "_backup_dirty", False)
        super().commit()
        self._backup_dirty = False
        if changed:
            mark_dirty()

    def rollback(self):
        super().rollback()
        self._backup_dirty = False

    def __exit__(self, exc_type, exc, tb):
        result = super().__exit__(exc_type, exc, tb)
        changed = getattr(self, "_backup_dirty", False)
        self._backup_dirty = False
        if exc_type is None and changed:
            mark_dirty()
        return result


def connection_factory(system_database):
    class ContentConnection(DirtyConnection):
        backup_system_database = system_database
    return ContentConnection
