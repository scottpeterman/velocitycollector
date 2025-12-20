-- SQLite Database Documentation
-- Database: collector.db
-- Generated: 2025-12-19 05:37:48
-- Path: /home/speterman/.vcollector/collector.db
================================================================================

-- SQLite Version: 3.45.1

-- TABLES
--------------------------------------------------------------------------------

-- Table: captures
----------------------------------------
-- Columns:
--   id: INTEGER (PRIMARY KEY)
--   device_id: INTEGER
--   device_name: TEXT NOT NULL
--   capture_type: TEXT NOT NULL
--   filepath: TEXT NOT NULL
--   file_size: INTEGER
--   captured_at: TEXT DEFAULT CURRENT_TIMESTAMP
--   job_history_id: INTEGER

-- Foreign Keys:
--   job_history_id -> job_history.id

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

-- Table: credentials
----------------------------------------
-- Columns:
--   id: INTEGER (PRIMARY KEY)
--   name: TEXT NOT NULL
--   username: TEXT NOT NULL
--   password_encrypted: TEXT
--   ssh_key_encrypted: TEXT
--   ssh_key_passphrase_encrypted: TEXT
--   is_default: INTEGER DEFAULT 0
--   created_at: TEXT DEFAULT CURRENT_TIMESTAMP
--   updated_at: TEXT DEFAULT CURRENT_TIMESTAMP

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

-- Table: job_commands
----------------------------------------
-- Columns:
--   id: INTEGER (PRIMARY KEY)
--   job_id: INTEGER NOT NULL
--   sequence: INTEGER NOT NULL DEFAULT 0
--   command: TEXT NOT NULL
--   description: TEXT
--   output_directory: TEXT
--   use_textfsm: INTEGER DEFAULT 0
--   textfsm_template: TEXT

-- Foreign Keys:
--   job_id -> jobs.id

CREATE TABLE job_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    sequence INTEGER NOT NULL DEFAULT 0,         -- Order of execution
    command TEXT NOT NULL,
    description TEXT,
    output_directory TEXT,                       -- Override per-command
    use_textfsm INTEGER DEFAULT 0,
    textfsm_template TEXT,
    
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    UNIQUE (job_id, sequence)
);

-- Table: job_history
----------------------------------------
-- Columns:
--   id: INTEGER (PRIMARY KEY)
--   job_id: TEXT NOT NULL
--   job_file: TEXT
--   started_at: TEXT NOT NULL
--   completed_at: TEXT
--   total_devices: INTEGER
--   success_count: INTEGER
--   failed_count: INTEGER
--   status: TEXT
--   error_message: TEXT

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

-- Table: job_tag_assignments
----------------------------------------
-- Columns:
--   job_id: INTEGER NOT NULL (PRIMARY KEY)
--   tag_id: INTEGER NOT NULL (PRIMARY KEY)

-- Foreign Keys:
--   tag_id -> job_tags.id
--   job_id -> jobs.id

CREATE TABLE job_tag_assignments (
    job_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY (job_id, tag_id),
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES job_tags(id) ON DELETE CASCADE
);

-- Table: job_tags
----------------------------------------
-- Columns:
--   id: INTEGER (PRIMARY KEY)
--   name: TEXT NOT NULL
--   color: TEXT DEFAULT '9e9e9e'

CREATE TABLE job_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    color TEXT DEFAULT '9e9e9e'
);

-- Table: jobs
----------------------------------------
-- Columns:
--   id: INTEGER (PRIMARY KEY)
--   name: TEXT NOT NULL
--   slug: TEXT NOT NULL
--   description: TEXT
--   capture_type: TEXT NOT NULL
--   vendor: TEXT
--   credential_id: INTEGER
--   credential_fallback_env: TEXT
--   protocol: TEXT NOT NULL DEFAULT 'ssh'
--   device_filter_source: TEXT DEFAULT 'database'
--   device_filter_platform_id: INTEGER
--   device_filter_site_id: INTEGER
--   device_filter_role_id: INTEGER
--   device_filter_name_pattern: TEXT
--   device_filter_status: TEXT DEFAULT 'active'
--   paging_disable_command: TEXT
--   command: TEXT NOT NULL
--   output_directory: TEXT
--   filename_pattern: TEXT DEFAULT '{device_name}.txt'
--   use_textfsm: INTEGER DEFAULT 0
--   textfsm_template: TEXT
--   validation_min_score: INTEGER DEFAULT 0
--   store_failures: INTEGER DEFAULT 1
--   max_workers: INTEGER DEFAULT 10
--   timeout_seconds: INTEGER DEFAULT 60
--   inter_command_delay: INTEGER DEFAULT 1
--   base_path: TEXT DEFAULT '~/.vcollector/collections'
--   schedule_enabled: INTEGER DEFAULT 0
--   schedule_cron: TEXT
--   is_enabled: INTEGER DEFAULT 1
--   last_run_at: TEXT
--   last_run_status: TEXT
--   legacy_job_id: INTEGER
--   legacy_job_file: TEXT
--   migrated_at: TEXT
--   created_at: TEXT NOT NULL DEFAULT datetime('now')
--   updated_at: TEXT NOT NULL DEFAULT datetime('now')

-- Foreign Keys:
--   credential_id -> credentials.id

CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Identity
    name TEXT NOT NULL,                          -- Human-readable name: "Arista ARP Collection"
    slug TEXT UNIQUE NOT NULL,                   -- URL-friendly: "arista-arp"
    description TEXT,
    
    -- Job Classification
    capture_type TEXT NOT NULL,                  -- 'arp', 'mac', 'config', 'inventory', 'routes', etc.
    vendor TEXT,                                 -- 'arista', 'cisco', 'juniper', NULL = multi-vendor
    
    -- Credentials
    credential_id INTEGER,                       -- FK to credentials table (NULL = use default)
    credential_fallback_env TEXT,                -- Environment variable fallback
    
    -- Connection Settings
    protocol TEXT NOT NULL DEFAULT 'ssh',        -- 'ssh', 'telnet', 'netconf', 'api'
    
    -- Device Filtering (which devices to run against)
    device_filter_source TEXT DEFAULT 'database', -- 'database', 'file', 'manual'
    device_filter_platform_id INTEGER,           -- FK to dcim_platform (NULL = all platforms for vendor)
    device_filter_site_id INTEGER,               -- FK to dcim_site (NULL = all sites)
    device_filter_role_id INTEGER,               -- FK to dcim_device_role (NULL = all roles)
    device_filter_name_pattern TEXT,             -- Regex pattern for device names
    device_filter_status TEXT DEFAULT 'active',  -- Only collect from devices with this status
    
    -- Commands
    paging_disable_command TEXT,                 -- e.g., 'terminal length 0'
    command TEXT NOT NULL,                       -- Main command(s) to execute
    
    -- Output Settings
    output_directory TEXT,                       -- Subdirectory under collections: 'arp', 'mac', etc.
    filename_pattern TEXT DEFAULT '{device_name}.txt',
    
    -- Validation / Parsing
    use_textfsm INTEGER DEFAULT 0,               -- Boolean: parse output with TextFSM
    textfsm_template TEXT,                       -- Template name/filter for TextFSM
    validation_min_score INTEGER DEFAULT 0,      -- Minimum quality score to accept
    store_failures INTEGER DEFAULT 1,            -- Store output even if validation fails
    
    -- Execution Settings
    max_workers INTEGER DEFAULT 10,              -- Concurrent connections
    timeout_seconds INTEGER DEFAULT 60,          -- Per-device timeout
    inter_command_delay INTEGER DEFAULT 1,       -- Seconds between commands
    
    -- Storage
    base_path TEXT DEFAULT '~/.vcollector/collections',
    
    -- Scheduling (for future cron-like functionality)
    schedule_enabled INTEGER DEFAULT 0,
    schedule_cron TEXT,                          -- Cron expression: '0 2 * * *'
    
    -- State
    is_enabled INTEGER DEFAULT 1,                -- Can be disabled without deleting
    last_run_at TEXT,                            -- Timestamp of last execution
    last_run_status TEXT,                        -- 'success', 'partial', 'failed'
    
    -- Legacy Migration Tracking
    legacy_job_id INTEGER,                       -- Original job_id from JSON (300, 301, etc.)
    legacy_job_file TEXT,                        -- Original filename
    migrated_at TEXT,                            -- When migrated from JSON
    
    -- Timestamps
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    
    -- Foreign Keys (optional - may reference dcim.db tables via ATTACH)
    FOREIGN KEY (credential_id) REFERENCES credentials(id) ON DELETE SET NULL
);

-- Table: vault_metadata
----------------------------------------
-- Columns:
--   id: INTEGER (PRIMARY KEY)
--   key: TEXT NOT NULL
--   value: TEXT

CREATE TABLE vault_metadata (
                id INTEGER PRIMARY KEY,
                key TEXT UNIQUE NOT NULL,
                value TEXT
            );

-- VIEWS
--------------------------------------------------------------------------------

-- View: v_job_history_detail
----------------------------------------
CREATE VIEW v_job_history_detail AS
SELECT 
    h.*,
    j.name AS job_name,
    j.capture_type,
    j.vendor
FROM job_history h
LEFT JOIN jobs j ON h.job_id = j.slug OR h.job_id = CAST(j.legacy_job_id AS TEXT);

-- View: v_job_summary
----------------------------------------
CREATE VIEW v_job_summary AS
SELECT 
    j.id,
    j.name,
    j.slug,
    j.capture_type,
    j.vendor,
    j.is_enabled,
    j.last_run_at,
    j.last_run_status,
    j.schedule_enabled,
    j.schedule_cron,
    c.name AS credential_name,
    (SELECT COUNT(*) FROM job_history h WHERE h.job_id = j.slug OR h.job_id = CAST(j.legacy_job_id AS TEXT)) AS run_count,
    (SELECT MAX(started_at) FROM job_history h WHERE h.job_id = j.slug OR h.job_id = CAST(j.legacy_job_id AS TEXT)) AS last_history_run
FROM jobs j
LEFT JOIN credentials c ON j.credential_id = c.id;

-- INDEXES
--------------------------------------------------------------------------------

-- Indexes for table: captures
----------------------------------------
-- Index: idx_captures_device
CREATE INDEX idx_captures_device ON captures(device_name);

-- Index: idx_captures_type
CREATE INDEX idx_captures_type ON captures(capture_type);

-- Indexes for table: credentials
----------------------------------------
-- Index: idx_credentials_default
CREATE INDEX idx_credentials_default ON credentials(is_default);

-- Index: idx_credentials_name
CREATE INDEX idx_credentials_name ON credentials(name);

-- Indexes for table: job_commands
----------------------------------------
-- Index: idx_job_commands_job
CREATE INDEX idx_job_commands_job ON job_commands(job_id);

-- Indexes for table: job_history
----------------------------------------
-- Index: idx_job_history_started
CREATE INDEX idx_job_history_started ON job_history(started_at);

-- Indexes for table: jobs
----------------------------------------
-- Index: idx_jobs_capture_type
CREATE INDEX idx_jobs_capture_type ON jobs(capture_type);

-- Index: idx_jobs_enabled
CREATE INDEX idx_jobs_enabled ON jobs(is_enabled);

-- Index: idx_jobs_legacy_id
CREATE INDEX idx_jobs_legacy_id ON jobs(legacy_job_id);

-- Index: idx_jobs_vendor
CREATE INDEX idx_jobs_vendor ON jobs(vendor);

-- SUMMARY
--------------------------------------------------------------------------------
-- Tables: 8
-- Views: 2
-- Indexes: 10
-- Triggers: 0
