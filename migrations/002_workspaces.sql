CREATE TABLE IF NOT EXISTS workspaces (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    slug          TEXT NOT NULL UNIQUE,
    owner_id      INTEGER NOT NULL REFERENCES users(id),
    server_ip     TEXT DEFAULT '165.22.123.55',
    server_user   TEXT DEFAULT 'root',
    created       TEXT NOT NULL,
    updated       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspace_members (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id  INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role          TEXT NOT NULL DEFAULT 'member',
    created       TEXT NOT NULL,
    UNIQUE(workspace_id, user_id)
);
