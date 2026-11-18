CREATE DATABASE IF NOT EXISTS admissionsim
    DEFAULT CHARACTER SET utf8mb4
    COLLATE utf8mb4_0900_as_cs;

USE admissionsim;

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
        FOREIGN KEY (junior_code) REFERENCES junior_schools (school_code)
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
        FOREIGN KEY (senior_code) REFERENCES senior_schools (school_code),
    CONSTRAINT fk_plan_junior
        FOREIGN KEY (source_junior_code) REFERENCES junior_schools (school_code),
    CONSTRAINT chk_plan_batch_no CHECK (batch_no BETWEEN 1 AND 4),
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
    CONSTRAINT uq_qualification_school_rank UNIQUE (senior_code, assessment_rank),
    CONSTRAINT chk_qualification_rank CHECK (assessment_rank >= 1),
    CONSTRAINT fk_qualification_candidate
        FOREIGN KEY (candidate_no) REFERENCES candidates (candidate_no),
    CONSTRAINT fk_qualification_senior
        FOREIGN KEY (senior_code) REFERENCES senior_schools (school_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE IF NOT EXISTS candidate_choices (
    candidate_no VARCHAR(24) NOT NULL,
    batch_no TINYINT NOT NULL,
    choice_no TINYINT NOT NULL,
    senior_code VARCHAR(16) NOT NULL,
    PRIMARY KEY (candidate_no, batch_no, choice_no),
    CONSTRAINT uq_choice_school UNIQUE (candidate_no, batch_no, senior_code),
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
        FOREIGN KEY (candidate_no) REFERENCES candidates (candidate_no),
    CONSTRAINT fk_choice_senior
        FOREIGN KEY (senior_code) REFERENCES senior_schools (school_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;