-- Editable Matrix AI waiting-assistant tag synonym groups.
CREATE TABLE IF NOT EXISTS matrix_ai_tag_synonym_groups (
  id INT AUTO_INCREMENT PRIMARY KEY,
  terms JSON NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO matrix_ai_tag_synonym_groups (terms)
SELECT '["monitor","screen","display"]'
WHERE NOT EXISTS (SELECT 1 FROM matrix_ai_tag_synonym_groups WHERE terms = '["monitor","screen","display"]');
INSERT INTO matrix_ai_tag_synonym_groups (terms)
SELECT '["trackpad","touchpad"]'
WHERE NOT EXISTS (SELECT 1 FROM matrix_ai_tag_synonym_groups WHERE terms = '["trackpad","touchpad"]');
INSERT INTO matrix_ai_tag_synonym_groups (terms)
SELECT '["computer","pc","workstation","desktop"]'
WHERE NOT EXISTS (SELECT 1 FROM matrix_ai_tag_synonym_groups WHERE terms = '["computer","pc","workstation","desktop"]');
INSERT INTO matrix_ai_tag_synonym_groups (terms)
SELECT '["notebook","laptop"]'
WHERE NOT EXISTS (SELECT 1 FROM matrix_ai_tag_synonym_groups WHERE terms = '["notebook","laptop"]');
INSERT INTO matrix_ai_tag_synonym_groups (terms)
SELECT '["cellphone","mobile","mobile phone","phone","smartphone"]'
WHERE NOT EXISTS (SELECT 1 FROM matrix_ai_tag_synonym_groups WHERE terms = '["cellphone","mobile","mobile phone","phone","smartphone"]');
INSERT INTO matrix_ai_tag_synonym_groups (terms)
SELECT '["printer","mfp","multifunction printer"]'
WHERE NOT EXISTS (SELECT 1 FROM matrix_ai_tag_synonym_groups WHERE terms = '["printer","mfp","multifunction printer"]');
INSERT INTO matrix_ai_tag_synonym_groups (terms)
SELECT '["wifi","wi fi","wi-fi","wireless"]'
WHERE NOT EXISTS (SELECT 1 FROM matrix_ai_tag_synonym_groups WHERE terms = '["wifi","wi fi","wi-fi","wireless"]');
INSERT INTO matrix_ai_tag_synonym_groups (terms)
SELECT '["internet","network","networking"]'
WHERE NOT EXISTS (SELECT 1 FROM matrix_ai_tag_synonym_groups WHERE terms = '["internet","network","networking"]');
INSERT INTO matrix_ai_tag_synonym_groups (terms)
SELECT '["email","e mail","e-mail","mail"]'
WHERE NOT EXISTS (SELECT 1 FROM matrix_ai_tag_synonym_groups WHERE terms = '["email","e mail","e-mail","mail"]');
