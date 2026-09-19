CREATE TABLE IF NOT EXISTS approval_configuration_technicians (
    configuration_id INT NOT NULL,
    user_id INT NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    PRIMARY KEY (configuration_id, user_id),
    CONSTRAINT fk_approval_technician_configuration FOREIGN KEY (configuration_id) REFERENCES approval_configurations(id) ON DELETE CASCADE,
    CONSTRAINT fk_approval_technician_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS approval_configuration_technical_roles (
    configuration_id INT NOT NULL,
    role_id INT NOT NULL,
    PRIMARY KEY (configuration_id, role_id),
    CONSTRAINT fk_approval_technical_role_configuration FOREIGN KEY (configuration_id) REFERENCES approval_configurations(id) ON DELETE CASCADE,
    CONSTRAINT fk_approval_technical_role FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS approval_configuration_company_roles (
    configuration_id INT NOT NULL,
    role_key VARCHAR(64) NOT NULL,
    PRIMARY KEY (configuration_id, role_key),
    CONSTRAINT fk_approval_company_role_configuration FOREIGN KEY (configuration_id) REFERENCES approval_configurations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS approval_configuration_job_titles (
    configuration_id INT NOT NULL,
    job_title VARCHAR(255) NOT NULL,
    PRIMARY KEY (configuration_id, job_title),
    CONSTRAINT fk_approval_job_title_configuration FOREIGN KEY (configuration_id) REFERENCES approval_configurations(id) ON DELETE CASCADE
);

INSERT INTO approval_configuration_technicians (configuration_id, user_id, sort_order)
SELECT id, technician_user_id, 0 FROM approval_configurations;
