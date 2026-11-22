USE admissionsim;

START TRANSACTION;

INSERT INTO junior_schools (school_code, school_name) VALUES
    ('J1', '松林初中'),
    ('J2', '云溪初中');

INSERT INTO senior_schools (school_code, school_name) VALUES
    ('A', '青禾高中'),
    ('B', '江湾高中'),
    ('C', '石桥高中'),
    ('D', '白鹭高中');

INSERT INTO candidates (candidate_no, candidate_name, junior_code, score, tie_no) VALUES
    ('S01', '方雨辰', 'J1', 700, 1),
    ('S02', '何予安', 'J2', 714, 1),
    ('S03', '叶以宁', 'J1', 760, 1),
    ('S04', '许思源', 'J2', 699, 1),
    ('S05', '郑沐阳', 'J1', 679, 1),
    ('S06', '黄子晴', 'J1', 730, 1),
    ('S07', '沈晨星', 'J1', 720, 1),
    ('S08', '莫知行', 'J2', 676, 1),
    ('S09', '温嘉文', 'J2', 748, 1),
    ('S10', '吴舒言', 'J1', 731, 1),
    ('S11', '林清悦', 'J1', 710, 1),
    ('S12', '梁芷宁', 'J2', 705, 1),
    ('S13', '陆欣桐', 'J1', 690, 1),
    ('S14', '钟晓棠', 'J2', 680, 1),
    ('S15', '周念慈', 'J1', 650, 1),
    ('S16', '陈雨禾', 'J2', 675, 1),
    ('S17', '张亦航', 'J1', 675, 3),
    ('S18', '苏书远', 'J2', 600, 1),
    ('S19', '杨秋然', 'J2', 675, 2),
    ('S20', '罗若安', 'J1', 599, 1);

INSERT INTO admission_plans
    (plan_id, batch_no, senior_code, source_junior_code, initial_seats, cutoff_score)
VALUES
    ('1-A',    1, 'A', NULL, 1, 700),
    ('1-B',    1, 'B', NULL, 1, 680),
    ('2-A-J1', 2, 'A', 'J1', 1, 600),
    ('2-A-J2', 2, 'A', 'J2', 2, 600),
    ('2-B-J1', 2, 'B', 'J1', 1, 600),
    ('2-B-J2', 2, 'B', 'J2', 2, 600),
    ('2-C-J1', 2, 'C', 'J1', 1, 600),
    ('2-C-J2', 2, 'C', 'J2', 1, 680),
    ('3-A',    3, 'A', NULL, 1, 600),
    ('3-B',    3, 'B', NULL, 1, 600),
    ('3-C',    3, 'C', NULL, 2, 600),
    ('4-D',    4, 'D', NULL, 3, 600);

INSERT INTO autonomous_qualifications (candidate_no, senior_code, assessment_rank) VALUES
    ('S01', 'A', 1),
    ('S02', 'A', 2),
    ('S04', 'A', 3),
    ('S05', 'B', 1);

INSERT INTO candidate_choices (candidate_no, batch_no, choice_no, senior_code) VALUES
    ('S01', 1, 1, 'A'), ('S01', 2, 1, 'A'), ('S01', 3, 1, 'A'), ('S01', 4, 1, 'D'),
    ('S02', 1, 1, 'A'), ('S02', 2, 1, 'A'), ('S02', 3, 1, 'A'), ('S02', 4, 1, 'D'),
    ('S03', 1, 1, 'A'), ('S03', 2, 1, 'B'), ('S03', 3, 1, 'B'), ('S03', 4, 1, 'D'),
    ('S04', 1, 1, 'A'), ('S04', 3, 1, 'B'),
    ('S05', 1, 1, 'B'), ('S05', 2, 1, 'B'), ('S05', 2, 2, 'C'), ('S05', 3, 1, 'C'),
    ('S06', 2, 1, 'A'), ('S06', 3, 1, 'A'),
    ('S07', 2, 1, 'A'), ('S07', 3, 1, 'A'), ('S07', 3, 2, 'B'),
    ('S08', 2, 1, 'B'), ('S08', 3, 1, 'B'), ('S08', 4, 1, 'D'),
    ('S09', 3, 1, 'A'),
    ('S10', 3, 1, 'A'),
    ('S11', 3, 1, 'A'), ('S11', 3, 2, 'B'), ('S11', 4, 1, 'D'),
    ('S12', 3, 1, 'A'), ('S12', 3, 2, 'B'),
    ('S13', 3, 1, 'B'),
    ('S14', 3, 1, 'B'),
    ('S15', 3, 1, 'C'),
    ('S16', 2, 1, 'C'), ('S16', 3, 1, 'A'), ('S16', 3, 2, 'C'),
    ('S17', 3, 1, 'C'),
    ('S18', 4, 1, 'D'),
    ('S19', 3, 1, 'A'), ('S19', 3, 2, 'C'),
    ('S20', 4, 1, 'D');

COMMIT;

SELECT 'junior_schools' AS item, COUNT(*) AS total FROM junior_schools
UNION ALL SELECT 'senior_schools', COUNT(*) FROM senior_schools
UNION ALL SELECT 'candidates', COUNT(*) FROM candidates
UNION ALL SELECT 'admission_plans', COUNT(*) FROM admission_plans
UNION ALL SELECT 'autonomous_qualifications', COUNT(*) FROM autonomous_qualifications
UNION ALL SELECT 'candidate_choices', COUNT(*) FROM candidate_choices;

SELECT batch_no, COUNT(*) AS plans, SUM(initial_seats) AS seats
FROM admission_plans
GROUP BY batch_no
ORDER BY batch_no;

SELECT SUM(initial_seats) AS total_initial_seats FROM admission_plans;