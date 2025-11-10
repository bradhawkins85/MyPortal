-- BCP Plan Overview table for storing plan metadata per company
CREATE TABLE IF NOT EXISTS bcp_plan_overview (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    title VARCHAR(255) NOT NULL DEFAULT 'Business Continuity Plan',
    executive_summary TEXT,
    version VARCHAR(50) DEFAULT '1.0',
    last_reviewed DATETIME,
    next_review DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    UNIQUE KEY unique_company_plan (company_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BCP Objectives table for storing plan objectives
CREATE TABLE IF NOT EXISTS bcp_objectives (
    id INT AUTO_INCREMENT PRIMARY KEY,
    plan_id INT NOT NULL,
    objective_text TEXT NOT NULL,
    display_order INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (plan_id) REFERENCES bcp_plan_overview(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- BCP Distribution List table
CREATE TABLE IF NOT EXISTS bcp_distribution_list (
    id INT AUTO_INCREMENT PRIMARY KEY,
    plan_id INT NOT NULL,
    copy_number VARCHAR(50) NOT NULL,
    name VARCHAR(255) NOT NULL,
    location VARCHAR(255),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (plan_id) REFERENCES bcp_plan_overview(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Add BCP view permission to roles table
ALTER TABLE roles
ADD COLUMN IF NOT EXISTS bcp_view BOOLEAN NOT NULL DEFAULT FALSE;

-- Add BCP edit permission to roles table  
ALTER TABLE roles
ADD COLUMN IF NOT EXISTS bcp_edit BOOLEAN NOT NULL DEFAULT FALSE;
