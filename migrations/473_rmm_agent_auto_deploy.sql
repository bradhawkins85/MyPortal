-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 473: push the RMM agent from MyPortal.
--
-- Technicians push the agent to one device from the Assets page; the tray app
-- on the device downloads it from MyPortal, enrols it with the tray device's
-- own token and installs the service. Each push is a tray command
-- ('install_rmm_agent') in tray_command_log, so its history and outcome live
-- there. With rmm_agent_auto_deploy on, MyPortal pushes the agent to every
-- tray device of the company that does not have one yet.
ALTER TABLE companies
  ADD COLUMN IF NOT EXISTS rmm_agent_auto_deploy TINYINT(1) NOT NULL DEFAULT 0;
