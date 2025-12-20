-- VelocityCollector Schema
-- Database: ~/.vcollector/collector.db

-- Vault metadata (salt, check value, iterations)
CREATE TABLE vault_metadata (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,
    value TEXT
);

-- Encrypted credentials
CREATE TABLE credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    username TEXT NOT NULL,
    password_encrypted TEXT,
    ssh_key_encrypted TEXT,
    ssh_key_passphrase_encrypted TEXT,
    is_default INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Job execution history
CREATE TABLE job_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    job_file TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    total_devices INTEGER,
    success_count INTEGER,
    failed_count INTEGER,
    status TEXT,
    error_message TEXT
);

-- Capture metadata
CREATE TABLE captures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER,
    device_name TEXT NOT NULL,
    capture_type TEXT NOT NULL,
    filepath TEXT NOT NULL,
    file_size INTEGER,
    captured_at TEXT DEFAULT CURRENT_TIMESTAMP,
    job_history_id INTEGER,
    FOREIGN KEY (job_history_id) REFERENCES job_history(id)
);

-- Indexes
CREATE INDEX idx_credentials_name ON credentials(name);
CREATE INDEX idx_credentials_default ON credentials(is_default);
CREATE INDEX idx_job_history_started ON job_history(started_at);
CREATE INDEX idx_captures_device ON captures(device_name);
CREATE INDEX idx_captures_type ON captures(capture_type);