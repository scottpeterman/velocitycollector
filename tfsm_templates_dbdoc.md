-- SQLite Database Documentation
-- Database: tfsm_templates.db
-- Generated: 2025-12-20 19:15:45
-- Path: vcollector/core/tfsm_templates.db
================================================================================

-- SQLite Version: 3.45.1

-- TABLES
--------------------------------------------------------------------------------

-- Table: templates
----------------------------------------
-- Columns:
--   id: INT
--   cli_command: TEXT
--   cli_content: TEXT
--   textfsm_content: TEXT
--   textfsm_hash: TEXT
--   source: TEXT
--   created: TEXT

CREATE TABLE "templates"(
  id INT,
  cli_command TEXT,
  cli_content TEXT,
  textfsm_content TEXT,
  textfsm_hash TEXT,
  source TEXT,
  created TEXT
);

-- SUMMARY
--------------------------------------------------------------------------------
-- Tables: 1
-- Views: 0
-- Indexes: 0
-- Triggers: 0
