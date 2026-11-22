-- 主系统独立数据库。由 manage.py init-db 在本机提示输入 root 密码后执行。
-- 不修改 admissionsim 或 admissionsim_scale_150000。
CREATE DATABASE IF NOT EXISTS admissionsim_app CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_as_cs;
USE admissionsim_app;

CREATE TABLE IF NOT EXISTS app_datasets (
    dataset_id CHAR(36) PRIMARY KEY,
    parent_id CHAR(36) NULL,
    label VARCHAR(200) NOT NULL,
    content_sha CHAR(64) NOT NULL,
    candidate_count INT UNSIGNED NOT NULL,
    payload LONGBLOB NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    FOREIGN KEY (parent_id) REFERENCES app_datasets(dataset_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS app_runs (
    run_id CHAR(36) PRIMARY KEY,
    dataset_id CHAR(36) NOT NULL,
    rules JSON NOT NULL,
    provenance JSON NOT NULL,
    completed_batch TINYINT UNSIGNED NOT NULL DEFAULT 0,
    candidate_count INT UNSIGNED NOT NULL,
    admitted_count INT UNSIGNED NOT NULL DEFAULT 0,
    remaining_seats INT UNSIGNED NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CHECK (completed_batch BETWEEN 0 AND 4),
    CHECK (admitted_count <= candidate_count),
    FOREIGN KEY (dataset_id) REFERENCES app_datasets(dataset_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS app_checkpoints (
    run_id CHAR(36) NOT NULL,
    batch_no TINYINT UNSIGNED NOT NULL,
    state_sha CHAR(64) NOT NULL,
    payload LONGBLOB NOT NULL,
    PRIMARY KEY (run_id, batch_no),
    CHECK (batch_no BETWEEN 0 AND 4),
    FOREIGN KEY (run_id) REFERENCES app_runs(run_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS app_plan_results (
    run_id CHAR(36) NOT NULL,
    plan_id VARCHAR(80) NOT NULL,
    batch TINYINT UNSIGNED NOT NULL,
    school VARCHAR(24) NOT NULL,
    junior VARCHAR(24) NULL,
    initial_seats INT UNSIGNED NOT NULL,
    transferred_in INT UNSIGNED NOT NULL,
    transferred_out INT UNSIGNED NOT NULL,
    admitted_count INT UNSIGNED NOT NULL,
    remaining_seats INT UNSIGNED NOT NULL,
    lowest_score INT UNSIGNED NULL,
    last_id VARCHAR(24) NULL,
    last_score INT UNSIGNED NULL,
    last_choice TINYINT UNSIGNED NULL,
    last_tie INT UNSIGNED NULL,
    PRIMARY KEY (run_id, plan_id),
    INDEX ix_plan_school (run_id, school, batch),
    CHECK (initial_seats + transferred_in = transferred_out + admitted_count + remaining_seats),
    FOREIGN KEY (run_id) REFERENCES app_runs(run_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS app_candidate_results (
    run_id CHAR(36) NOT NULL,
    candidate_no VARCHAR(24) NOT NULL,
    name VARCHAR(100) NOT NULL,
    junior_code VARCHAR(24) NOT NULL,
    score INT UNSIGNED NOT NULL,
    tie_no INT UNSIGNED NOT NULL,
    admitted_batch TINYINT UNSIGNED NULL,
    senior_code VARCHAR(24) NULL,
    plan_id VARCHAR(80) NULL,
    admission_order INT UNSIGNED NULL,
    choice_no TINYINT UNSIGNED NULL,
    payload JSON NOT NULL,
    PRIMARY KEY (run_id, candidate_no),
    UNIQUE KEY uq_actual_order (run_id, plan_id, admission_order),
    INDEX ix_candidate_junior (run_id, junior_code),
    INDEX ix_candidate_senior (run_id, senior_code, admitted_batch),
    INDEX ix_candidate_batch (run_id, admitted_batch, candidate_no),
    INDEX ix_candidate_statistics (run_id, junior_code, score, admitted_batch),
    CHECK ((plan_id IS NULL AND admitted_batch IS NULL AND admission_order IS NULL AND choice_no IS NULL AND senior_code IS NULL)
        OR (plan_id IS NOT NULL AND admitted_batch BETWEEN 1 AND 4 AND admission_order >= 1 AND choice_no BETWEEN 1 AND 6 AND senior_code IS NOT NULL)),
    FOREIGN KEY (run_id) REFERENCES app_runs(run_id),
    FOREIGN KEY (run_id, plan_id) REFERENCES app_plan_results(run_id, plan_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS app_transfers (
    run_id CHAR(36) NOT NULL,
    transfer_no INT UNSIGNED NOT NULL,
    source_plan_id VARCHAR(80) NOT NULL,
    target_plan_id VARCHAR(80) NOT NULL,
    seats INT UNSIGNED NOT NULL,
    PRIMARY KEY (run_id, transfer_no),
    UNIQUE KEY uq_transfer_source (run_id, source_plan_id),
    CHECK (seats > 0),
    FOREIGN KEY (run_id, source_plan_id) REFERENCES app_plan_results(run_id, plan_id),
    FOREIGN KEY (run_id, target_plan_id) REFERENCES app_plan_results(run_id, plan_id)
) ENGINE=InnoDB;

-- 复用既有应用账号，不创建新密码。
GRANT SELECT, INSERT, UPDATE, DELETE ON admissionsim_app.* TO 'admission_app'@'127.0.0.1';
