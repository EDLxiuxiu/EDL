USE admissionsim;

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
        FOREIGN KEY (run_id) REFERENCES admission_runs (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE IF NOT EXISTS run_admission_details (
    run_id CHAR(36) NOT NULL,
    candidate_no VARCHAR(24) NOT NULL,
    plan_id VARCHAR(40) NOT NULL,
    admission_order INT UNSIGNED NOT NULL,
    PRIMARY KEY (run_id, candidate_no),
    CONSTRAINT uq_run_plan_admission_order UNIQUE (run_id, plan_id, admission_order),
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