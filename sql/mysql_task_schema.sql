CREATE DATABASE IF NOT EXISTS intent DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE intent;

CREATE TABLE IF NOT EXISTS task_runtime_status (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  task_id VARCHAR(128) NOT NULL UNIQUE,
  task_type VARCHAR(64) NOT NULL DEFAULT 'video_infer',
  state VARCHAR(32) NOT NULL DEFAULT 'running' COMMENT 'running/ended',
  started_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  ended_at TIMESTAMP(3) NULL,
  last_update_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_last_update(last_update_at),
  INDEX idx_state(state)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS task_runtime_metrics (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  task_id VARCHAR(128) NOT NULL,
  role VARCHAR(32) NOT NULL COMMENT 'sender/receiver/trainer',
  sample_count BIGINT NOT NULL DEFAULT 0,
  infer_avg_ms DOUBLE NULL,
  infer_p95_ms DOUBLE NULL,
  rtt_avg_ms DOUBLE NULL,
  rtt_p95_ms DOUBLE NULL,
  latest_rtt_ms DOUBLE NULL,
  meet_target_count BIGINT NULL,
  meet_target_ratio DOUBLE NULL,
  target_latency_ms DOUBLE NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_task_role(task_id, role),
  INDEX idx_task_id(task_id)
) ENGINE=InnoDB;
