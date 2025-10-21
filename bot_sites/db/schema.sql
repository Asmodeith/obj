-- mirrorhub/db/schema.sql

PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS content (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    subtitle   TEXT NOT NULL,
    contacts   TEXT NOT NULL,         -- JSON string
    footer     TEXT NOT NULL,
    updated_at TEXT NOT NULL          -- ISO
);

CREATE TABLE IF NOT EXISTS domain (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    host       TEXT NOT NULL UNIQUE,
    status     TEXT NOT NULL CHECK (status IN ('active','hot','blocked')),
    ssl_ok     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_domain_status ON domain(status);

CREATE TABLE IF NOT EXISTS event_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    payload    TEXT NOT NULL,         -- JSON string
    created_at TEXT NOT NULL
);
