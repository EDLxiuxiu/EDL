CREATE DATABASE IF NOT EXISTS admissionsim_scale_150000
    DEFAULT CHARACTER SET utf8mb4
    COLLATE utf8mb4_0900_as_cs;

USE admissionsim_scale_150000;

-- 一、基础输入表

CREATE TABLE IF NOT EXISTS junior_schools (
    school_code VARCHAR(16) NOT NULL,
    school_name VARCHAR(100) NOT NULL,
    PRIMARY KEY (school_code),
    CONSTRAINT chk_junior_school_code
        CHECK (school_code <> '' AND LOCATE('-', school_code) = 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE IF NOT EXISTS senior_schools (
    school_code VARCHAR(16) NOT NULL,
    school_name VARCHAR(100) NOT NULL,
    PRIMARY KEY (school_code),
    CONSTRAINT chk_senior_school_code
        CHECK (school_code <> '' AND LOCATE('-', school_code) = 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE IF NOT EXISTS candidates (
    candidate_no VARCHAR(24) NOT NULL,
    candidate_name VARCHAR(100) NOT NULL,
    junior_code VARCHAR(16) NOT NULL,
    score INT NOT NULL,
    tie_no INT NOT NULL,
    PRIMARY KEY (candidate_no),
    CONSTRAINT uq_candidate_score_tie UNIQUE (score, tie_no),
    CONSTRAINT chk_candidate_score CHECK (score >= 0),
    CONSTRAINT chk_candidate_tie_no CHECK (tie_no >= 1),
    CONSTRAINT fk_candidate_junior
        FOREIGN KEY (junior_code)
        REFERENCES junior_schools (school_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE IF NOT EXISTS admission_plans (
    plan_id VARCHAR(40) NOT NULL,
    batch_no TINYINT NOT NULL,
    senior_code VARCHAR(16) NOT NULL,
    source_junior_code VARCHAR(16) NULL,
    initial_seats INT NOT NULL,
    cutoff_score INT NOT NULL,
    PRIMARY KEY (plan_id),
    CONSTRAINT fk_plan_senior
        FOREIGN KEY (senior_code)
        REFERENCES senior_schools (school_code),
    CONSTRAINT fk_plan_junior
        FOREIGN KEY (source_junior_code)
        REFERENCES junior_schools (school_code),
    CONSTRAINT chk_plan_batch_no
        CHECK (batch_no BETWEEN 1 AND 4),
    CONSTRAINT chk_plan_source_junior CHECK (
        (batch_no = 2 AND source_junior_code IS NOT NULL)
        OR
        (batch_no <> 2 AND source_junior_code IS NULL)
    ),
    CONSTRAINT chk_plan_id CHECK (
        plan_id = CONCAT_WS('-', batch_no, senior_code, source_junior_code)
    ),
    CONSTRAINT chk_plan_initial_seats CHECK (initial_seats >= 0),
    CONSTRAINT chk_plan_cutoff_score CHECK (cutoff_score >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE IF NOT EXISTS autonomous_qualifications (
    candidate_no VARCHAR(24) NOT NULL,
    senior_code VARCHAR(16) NOT NULL,
    assessment_rank INT NOT NULL,
    PRIMARY KEY (candidate_no, senior_code),
    CONSTRAINT uq_qualification_school_rank
        UNIQUE (senior_code, assessment_rank),
    CONSTRAINT chk_qualification_rank CHECK (assessment_rank >= 1),
    CONSTRAINT fk_qualification_candidate
        FOREIGN KEY (candidate_no)
        REFERENCES candidates (candidate_no),
    CONSTRAINT fk_qualification_senior
        FOREIGN KEY (senior_code)
        REFERENCES senior_schools (school_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE IF NOT EXISTS candidate_choices (
    candidate_no VARCHAR(24) NOT NULL,
    batch_no TINYINT NOT NULL,
    choice_no TINYINT NOT NULL,
    senior_code VARCHAR(16) NOT NULL,
    PRIMARY KEY (candidate_no, batch_no, choice_no),
    CONSTRAINT uq_choice_school
        UNIQUE (candidate_no, batch_no, senior_code),
    CONSTRAINT chk_choice_no CHECK (
        choice_no BETWEEN 1 AND CASE batch_no
            WHEN 1 THEN 1
            WHEN 2 THEN 3
            WHEN 3 THEN 6
            WHEN 4 THEN 6
            ELSE 0
        END
    ),
    CONSTRAINT fk_choice_candidate
        FOREIGN KEY (candidate_no)
        REFERENCES candidates (candidate_no),
    CONSTRAINT fk_choice_senior
        FOREIGN KEY (senior_code)
        REFERENCES senior_schools (school_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

-- 二、实验数据集清单
-- 此实验库只保存一套输入数据，固定使用 dataset_slot = 1。
-- 后续由导入程序在同一导入事务中写入清单。

CREATE TABLE IF NOT EXISTS experiment_dataset (
    dataset_slot TINYINT UNSIGNED NOT NULL,
    dataset_id VARCHAR(80) NOT NULL,
    data_sha256 CHAR(64) NOT NULL,
    manifest JSON NOT NULL,
    imported_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (dataset_slot),
    CONSTRAINT uq_experiment_dataset_id UNIQUE (dataset_id),
    CONSTRAINT chk_experiment_dataset_slot CHECK (dataset_slot = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

-- 三、运行结果表
-- 保持现有结果模型，先建立优化前的数据库基准。

CREATE TABLE IF NOT EXISTS admission_runs (
    run_id CHAR(36) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    candidate_count INT UNSIGNED NOT NULL,
    admitted_count INT UNSIGNED NOT NULL,
    input_snapshot JSON NOT NULL,
    decision_log JSON NOT NULL,
    PRIMARY KEY (run_id),
    CONSTRAINT chk_run_counts CHECK (admitted_count <= candidate_count)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE IF NOT EXISTS run_plan_balances (
    run_id CHAR(36) NOT NULL,
    plan_id VARCHAR(40) NOT NULL,
    remaining_seats INT UNSIGNED NOT NULL,
    PRIMARY KEY (run_id, plan_id),
    CONSTRAINT fk_run_balance_run
        FOREIGN KEY (run_id)
        REFERENCES admission_runs (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE IF NOT EXISTS run_admission_details (
    run_id CHAR(36) NOT NULL,
    candidate_no VARCHAR(24) NOT NULL,
    plan_id VARCHAR(40) NOT NULL,
    admission_order INT UNSIGNED NOT NULL,
    PRIMARY KEY (run_id, candidate_no),
    CONSTRAINT uq_run_plan_admission_order
        UNIQUE (run_id, plan_id, admission_order),
    CONSTRAINT chk_run_admission_order CHECK (admission_order >= 1),
    CONSTRAINT fk_run_detail_plan
        FOREIGN KEY (run_id, plan_id)
        REFERENCES run_plan_balances (run_id, plan_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE IF NOT EXISTS run_seat_transfers (
    run_id CHAR(36) NOT NULL,
    transfer_no INT UNSIGNED NOT NULL,
    source_plan_id VARCHAR(40) NOT NULL,
    target_plan_id VARCHAR(40) NOT NULL,
    seats INT UNSIGNED NOT NULL,
    PRIMARY KEY (run_id, transfer_no),
    CONSTRAINT chk_run_transfer_seats CHECK (seats > 0),
    CONSTRAINT fk_run_transfer_source
        FOREIGN KEY (run_id, source_plan_id)
        REFERENCES run_plan_balances (run_id, plan_id),
    CONSTRAINT fk_run_transfer_target
        FOREIGN KEY (run_id, target_plan_id)
        REFERENCES run_plan_balances (run_id, plan_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

-- 四、给现有应用账号授予实验库的数据操作权限

GRANT SELECT, INSERT, UPDATE, DELETE
ON admissionsim_scale_150000.*
TO 'admission_app'@'127.0.0.1';

-- 五、验收查询

SELECT DATABASE() AS current_database;

SHOW TABLES;

SELECT 'junior_schools' AS table_name, COUNT(*) AS row_count
FROM junior_schools
UNION ALL
SELECT 'senior_schools', COUNT(*) FROM senior_schools
UNION ALL
SELECT 'candidates', COUNT(*) FROM candidates
UNION ALL
SELECT 'admission_plans', COUNT(*) FROM admission_plans
UNION ALL
SELECT 'autonomous_qualifications', COUNT(*) FROM autonomous_qualifications
UNION ALL
SELECT 'candidate_choices', COUNT(*) FROM candidate_choices
UNION ALL
SELECT 'experiment_dataset', COUNT(*) FROM experiment_dataset
UNION ALL
SELECT 'admission_runs', COUNT(*) FROM admission_runs
UNION ALL
SELECT 'run_plan_balances', COUNT(*) FROM run_plan_balances
UNION ALL
SELECT 'run_admission_details', COUNT(*) FROM run_admission_details
UNION ALL
SELECT 'run_seat_transfers', COUNT(*) FROM run_seat_transfers;

-- 核对原样例仍然存在，仅查询，不修改原库。

SELECT COUNT(*) AS original_sample_candidates
FROM admissionsim.candidates;

SELECT COUNT(*) AS original_saved_run
FROM admissionsim.admission_runs
WHERE run_id = 'fc128695-32d3-485c-86d1-2b09f2608cab';