CREATE DATABASE IF NOT EXISTS video_infer DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE video_infer;

CREATE TABLE IF NOT EXISTS task_deployments (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  task_id VARCHAR(128) NOT NULL UNIQUE,
  task_type VARCHAR(64) NOT NULL DEFAULT 'video_infer',
  status VARCHAR(32) NOT NULL DEFAULT 'created',
  scheduler_job_id VARCHAR(128) NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  started_at TIMESTAMP NULL,
  finished_at TIMESTAMP NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  params_json JSON NULL,
  remark VARCHAR(255) NULL,
  INDEX idx_status(status),
  INDEX idx_created_at(created_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS task_nodes (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  task_id VARCHAR(128) NOT NULL,
  node_id VARCHAR(128) NOT NULL,
  node_role VARCHAR(64) NOT NULL,
  node_ip VARCHAR(64) NOT NULL,
  node_port INT NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  started_at TIMESTAMP NULL,
  heartbeat_at TIMESTAMP NULL,
  finished_at TIMESTAMP NULL,
  runtime_params_json JSON NULL,
  UNIQUE KEY uk_task_node_role(task_id, node_role),
  INDEX idx_task_id(task_id),
  INDEX idx_status(status),
  CONSTRAINT fk_task_nodes_task_id FOREIGN KEY (task_id) REFERENCES task_deployments(task_id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS task_runtime_events (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  task_id VARCHAR(128) NOT NULL,
  node_name VARCHAR(128) NOT NULL,
  event_type VARCHAR(64) NOT NULL,
  event_ts TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  frame_id INT NULL,
  latency_ms DOUBLE NULL,
  payload JSON NULL,
  INDEX idx_task_ts(task_id, event_ts),
  INDEX idx_task_event(task_id, event_type),
  INDEX idx_event_type(event_type)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS task_runtime_agg (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  task_id VARCHAR(128) NOT NULL,
  window_type VARCHAR(16) NOT NULL COMMENT '10s/1m/all',
  sample_count BIGINT NOT NULL DEFAULT 0,
  avg_latency_ms DOUBLE NULL,
  p95_latency_ms DOUBLE NULL,
  max_latency_ms DOUBLE NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_task_window(task_id, window_type),
  INDEX idx_task_id(task_id),
  CONSTRAINT fk_task_runtime_agg_task_id FOREIGN KEY (task_id) REFERENCES task_deployments(task_id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB;
