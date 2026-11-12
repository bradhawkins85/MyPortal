-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Resolve company role rules against current, active staff records.
CREATE TABLE IF NOT EXISTS company_job_title_access (
    company_id INT NOT NULL,
    job_title VARCHAR(255) NOT NULL,
    role_id INT NOT NULL,
    staff_permission INT NOT NULL DEFAULT 0,
    PRIMARY KEY (company_id, job_title),
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE RESTRICT
);

DROP VIEW IF EXISTS effective_company_memberships;
DROP VIEW IF EXISTS company_job_title_memberships;

CREATE VIEW company_job_title_memberships AS
SELECT DISTINCT a.company_id, u.id AS user_id, a.role_id, a.job_title, a.staff_permission
FROM company_job_title_access AS a
INNER JOIN staff AS s ON s.company_id = a.company_id
    AND LOWER(TRIM(s.job_title)) = a.job_title
INNER JOIN users AS u ON u.company_id = s.company_id
    AND LOWER(TRIM(u.email)) = LOWER(TRIM(s.email))
WHERE s.enabled = 1 AND COALESCE(s.is_ex_staff, 0) = 0
    AND NOT EXISTS (
        SELECT 1 FROM staff AS newer
        WHERE newer.company_id = s.company_id
          AND LOWER(TRIM(newer.email)) = LOWER(TRIM(s.email))
          AND newer.enabled = 1 AND COALESCE(newer.is_ex_staff, 0) = 0
          AND newer.id > s.id
    );

CREATE VIEW effective_company_memberships AS
SELECT m.id, m.company_id, m.user_id,
       CASE WHEN m.status = 'active' THEN COALESCE(a.role_id, m.role_id) ELSE m.role_id END AS role_id,
       m.status, m.invited_by, m.invited_at, m.joined_at, m.last_seen_at,
       CASE WHEN m.status = 'active' THEN a.job_title ELSE NULL END AS job_title,
       CASE WHEN m.status = 'active' THEN a.staff_permission ELSE NULL END AS staff_permission
FROM company_memberships AS m
LEFT JOIN company_job_title_memberships AS a
    ON a.company_id = m.company_id AND a.user_id = m.user_id
UNION ALL
SELECT -a.user_id AS id, a.company_id, a.user_id, a.role_id,
       'active' AS status, NULL, NULL, NULL, NULL, a.job_title, a.staff_permission
FROM company_job_title_memberships AS a
WHERE NOT EXISTS (
    SELECT 1 FROM company_memberships AS m
    WHERE m.company_id = a.company_id AND m.user_id = a.user_id
);
