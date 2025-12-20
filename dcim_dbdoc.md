-- SQLite Database Documentation
-- Database: dcim.db
-- Generated: 2025-12-18 02:20:15
-- Path: /home/speterman/.vcollector/dcim.db
================================================================================

-- SQLite Version: 3.45.1

-- TABLES
--------------------------------------------------------------------------------

-- Table: dcim_device
----------------------------------------
-- Columns:
--   id: INTEGER (PRIMARY KEY)
--   name: TEXT NOT NULL
--   site_id: INTEGER NOT NULL
--   platform_id: INTEGER
--   role_id: INTEGER
--   status: TEXT NOT NULL DEFAULT 'active'
--   serial_number: TEXT
--   asset_tag: TEXT
--   primary_ip4: TEXT
--   primary_ip6: TEXT
--   oob_ip: TEXT
--   credential_id: INTEGER
--   ssh_port: INTEGER DEFAULT 22
--   description: TEXT
--   comments: TEXT
--   netbox_id: INTEGER
--   created_at: TEXT NOT NULL DEFAULT datetime('now')
--   updated_at: TEXT NOT NULL DEFAULT datetime('now')
--   last_collected_at: TEXT

-- Foreign Keys:
--   role_id -> dcim_device_role.id
--   platform_id -> dcim_platform.id
--   site_id -> dcim_site.id

CREATE TABLE dcim_device (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    
    -- Required relationships
    site_id INTEGER NOT NULL,
    
    -- Optional relationships
    platform_id INTEGER,                    -- OS/software platform
    role_id INTEGER,                        -- Functional role
    
    -- Device attributes
    status TEXT NOT NULL DEFAULT 'active',  -- active, planned, staged, failed, offline, decommissioning, inventory
    serial_number TEXT,
    asset_tag TEXT UNIQUE,
    
    -- Network identity (critical for collection)
    primary_ip4 TEXT,                       -- IPv4 management address
    primary_ip6 TEXT,                       -- IPv6 management address
    oob_ip TEXT,                            -- Out-of-band management IP
    
    -- Collection settings
    credential_id INTEGER,                  -- FK to credentials table (NULL = use site/global default)
    ssh_port INTEGER DEFAULT 22,
    
    -- Metadata
    description TEXT,
    comments TEXT,
    
    -- NetBox sync
    netbox_id INTEGER UNIQUE,
    
    -- Timestamps
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_collected_at TEXT,                 -- Last successful collection
    
    -- Constraints
    FOREIGN KEY (site_id) REFERENCES dcim_site(id) ON DELETE CASCADE,
    FOREIGN KEY (platform_id) REFERENCES dcim_platform(id) ON DELETE SET NULL,
    FOREIGN KEY (role_id) REFERENCES dcim_device_role(id) ON DELETE SET NULL,
    
    -- Name must be unique within site (NetBox behavior)
    UNIQUE (name, site_id)
);

-- Table: dcim_device_role
----------------------------------------
-- Columns:
--   id: INTEGER (PRIMARY KEY)
--   name: TEXT NOT NULL
--   slug: TEXT NOT NULL
--   color: TEXT DEFAULT '9e9e9e'
--   description: TEXT
--   netbox_id: INTEGER
--   created_at: TEXT NOT NULL DEFAULT datetime('now')
--   updated_at: TEXT NOT NULL DEFAULT datetime('now')

CREATE TABLE dcim_device_role (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    color TEXT DEFAULT '9e9e9e',            -- Hex color for UI (NetBox convention)
    description TEXT,
    
    -- NetBox sync
    netbox_id INTEGER UNIQUE,
    
    -- Timestamps
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Table: dcim_manufacturer
----------------------------------------
-- Columns:
--   id: INTEGER (PRIMARY KEY)
--   name: TEXT NOT NULL
--   slug: TEXT NOT NULL
--   description: TEXT
--   netbox_id: INTEGER
--   created_at: TEXT NOT NULL DEFAULT datetime('now')
--   updated_at: TEXT NOT NULL DEFAULT datetime('now')

CREATE TABLE dcim_manufacturer (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    description TEXT,
    
    -- NetBox sync
    netbox_id INTEGER UNIQUE,
    
    -- Timestamps
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Table: dcim_platform
----------------------------------------
-- Columns:
--   id: INTEGER (PRIMARY KEY)
--   name: TEXT NOT NULL
--   slug: TEXT NOT NULL
--   manufacturer_id: INTEGER
--   description: TEXT
--   netmiko_device_type: TEXT
--   paging_disable_command: TEXT
--   netbox_id: INTEGER
--   created_at: TEXT NOT NULL DEFAULT datetime('now')
--   updated_at: TEXT NOT NULL DEFAULT datetime('now')

-- Foreign Keys:
--   manufacturer_id -> dcim_manufacturer.id

CREATE TABLE dcim_platform (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,              -- Display name: "Cisco IOS"
    slug TEXT NOT NULL UNIQUE,              -- URL-friendly: "cisco_ios"
    manufacturer_id INTEGER,                -- Optional - can be NULL
    description TEXT,
    
    -- Collection-specific fields (not in NetBox, but needed for SSH)
    netmiko_device_type TEXT,               -- e.g., 'cisco_ios', 'arista_eos'
    paging_disable_command TEXT,            -- e.g., 'terminal length 0'
    
    -- NetBox sync
    netbox_id INTEGER UNIQUE,
    
    -- Timestamps
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    
    FOREIGN KEY (manufacturer_id) REFERENCES dcim_manufacturer(id) ON DELETE SET NULL
);

-- Table: dcim_site
----------------------------------------
-- Columns:
--   id: INTEGER (PRIMARY KEY)
--   name: TEXT NOT NULL
--   slug: TEXT NOT NULL
--   status: TEXT NOT NULL DEFAULT 'active'
--   description: TEXT
--   physical_address: TEXT
--   facility: TEXT
--   time_zone: TEXT
--   netbox_id: INTEGER
--   created_at: TEXT NOT NULL DEFAULT datetime('now')
--   updated_at: TEXT NOT NULL DEFAULT datetime('now')

CREATE TABLE dcim_site (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active',  -- active, planned, staging, decommissioning, retired
    description TEXT,
    physical_address TEXT,
    facility TEXT,                          -- Data center/facility code
    time_zone TEXT,                         -- e.g., 'America/Denver'
    
    -- NetBox sync
    netbox_id INTEGER UNIQUE,               -- ID in NetBox for sync operations
    
    -- Timestamps
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Table: schema_version
----------------------------------------
-- Columns:
--   version: INTEGER (PRIMARY KEY)
--   applied_at: TEXT NOT NULL DEFAULT datetime('now')

CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- VIEWS
--------------------------------------------------------------------------------

-- View: v_device_detail
----------------------------------------
CREATE VIEW v_device_detail AS
SELECT 
    d.id,
    d.name,
    d.status,
    d.primary_ip4,
    d.primary_ip6,
    d.oob_ip,
    d.ssh_port,
    d.serial_number,
    d.asset_tag,
    d.credential_id,
    d.description,
    d.last_collected_at,
    d.netbox_id,
    d.created_at,
    d.updated_at,
    -- Site info
    s.id AS site_id,
    s.name AS site_name,
    s.slug AS site_slug,
    -- Platform info
    p.id AS platform_id,
    p.name AS platform_name,
    p.slug AS platform_slug,
    p.netmiko_device_type,
    p.paging_disable_command,
    -- Manufacturer info (via platform)
    m.id AS manufacturer_id,
    m.name AS manufacturer_name,
    m.slug AS manufacturer_slug,
    -- Role info
    r.id AS role_id,
    r.name AS role_name,
    r.slug AS role_slug
FROM dcim_device d
LEFT JOIN dcim_site s ON d.site_id = s.id
LEFT JOIN dcim_platform p ON d.platform_id = p.id
LEFT JOIN dcim_manufacturer m ON p.manufacturer_id = m.id
LEFT JOIN dcim_device_role r ON d.role_id = r.id;

-- View: v_platform_device_counts
----------------------------------------
CREATE VIEW v_platform_device_counts AS
SELECT 
    p.id,
    p.name,
    p.slug,
    m.name AS manufacturer_name,
    COUNT(d.id) AS device_count
FROM dcim_platform p
LEFT JOIN dcim_manufacturer m ON p.manufacturer_id = m.id
LEFT JOIN dcim_device d ON p.id = d.platform_id
GROUP BY p.id;

-- View: v_site_device_counts
----------------------------------------
CREATE VIEW v_site_device_counts AS
SELECT 
    s.id,
    s.name,
    s.slug,
    s.status,
    COUNT(d.id) AS device_count,
    COUNT(CASE WHEN d.status = 'active' THEN 1 END) AS active_devices
FROM dcim_site s
LEFT JOIN dcim_device d ON s.id = d.site_id
GROUP BY s.id;

-- INDEXES
--------------------------------------------------------------------------------

-- Indexes for table: dcim_device
----------------------------------------
-- Index: idx_dcim_device_name
CREATE INDEX idx_dcim_device_name ON dcim_device(name);

-- Index: idx_dcim_device_netbox_id
CREATE INDEX idx_dcim_device_netbox_id ON dcim_device(netbox_id);

-- Index: idx_dcim_device_platform
CREATE INDEX idx_dcim_device_platform ON dcim_device(platform_id);

-- Index: idx_dcim_device_primary_ip4
CREATE INDEX idx_dcim_device_primary_ip4 ON dcim_device(primary_ip4);

-- Index: idx_dcim_device_role
CREATE INDEX idx_dcim_device_role ON dcim_device(role_id);

-- Index: idx_dcim_device_site
CREATE INDEX idx_dcim_device_site ON dcim_device(site_id);

-- Index: idx_dcim_device_status
CREATE INDEX idx_dcim_device_status ON dcim_device(status);

-- Indexes for table: dcim_device_role
----------------------------------------
-- Index: idx_dcim_device_role_slug
CREATE INDEX idx_dcim_device_role_slug ON dcim_device_role(slug);

-- Indexes for table: dcim_manufacturer
----------------------------------------
-- Index: idx_dcim_manufacturer_slug
CREATE INDEX idx_dcim_manufacturer_slug ON dcim_manufacturer(slug);

-- Indexes for table: dcim_platform
----------------------------------------
-- Index: idx_dcim_platform_manufacturer
CREATE INDEX idx_dcim_platform_manufacturer ON dcim_platform(manufacturer_id);

-- Index: idx_dcim_platform_slug
CREATE INDEX idx_dcim_platform_slug ON dcim_platform(slug);

-- Indexes for table: dcim_site
----------------------------------------
-- Index: idx_dcim_site_netbox_id
CREATE INDEX idx_dcim_site_netbox_id ON dcim_site(netbox_id);

-- Index: idx_dcim_site_slug
CREATE INDEX idx_dcim_site_slug ON dcim_site(slug);

-- Index: idx_dcim_site_status
CREATE INDEX idx_dcim_site_status ON dcim_site(status);

-- SUMMARY
--------------------------------------------------------------------------------
-- Tables: 6
-- Views: 3
-- Indexes: 14
-- Triggers: 0
