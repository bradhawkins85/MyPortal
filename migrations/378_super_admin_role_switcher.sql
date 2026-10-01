ALTER TABLE user_sessions
  ADD COLUMN selected_role_id INT NULL;

ALTER TABLE user_sessions
  ADD CONSTRAINT fk_user_sessions_selected_role
  FOREIGN KEY (selected_role_id) REFERENCES roles(id) ON DELETE SET NULL;
