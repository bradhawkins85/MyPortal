Apps should be designed with a 3 part layout, unless otherwise specified, there will be a left menu with buttons and relevant icons, a right header which will contain page specific menus, right body which will contain the actual app data.

Always consider security when implementing changes and factor in security best practices.

Apps should be themeable, with custom fav icons and logos.

The first user created for an app is the super admin and the initial login page will redirect to a registration page if there are no existing users.

Dates and times should be stored in the database in UTC format but displayed in local timezone.

Include a CURD Swagger UI for all API endpoints, update the API documentation when any API endpoints are created or modified,

Apps should not exceed the width or height of the viewport, unless explicitly specified.

Always include, and updated where required, installation and update scripts. These should be able to be executed from the server console and web ui. They will also obtain credentials where required from env file to pull code from github private repos. Install scripts should create systemd services to allow the app to run as a service. Also include a devlopment installer that runs alongside the production environment for testing purposes, this should not interact with the primary database.
Install scripts should check and if required install any python requirements such as python3 and venv

Maintain a change log for each new feature that is added to an app, also note if the change is a Fix or a Feature. The change log should be stored in a folder called changes and each change should generate a new file with a GUID. These files are to be imported to a change_log table in the database. If a changes.md file already exists import this to the database as well.

change log files should use the JSON format:
{
  "guid": "",
  "occurred_at": "",
  "change_type": "",
  "summary": "",
  "content_hash": ""
}

External webhook calls should be monitored and retried in the event of failures, the monitoring should be accessible via an admin page.

When working with SQL, any migrations should be applied during the applications' startup process automatically. Apps should use SQLite as a fallback database when MySQL is not configured.

Code should be based on/around Python.

Apps should be designed with a responsive layout.

All tables should have sorting and filtering capabilities.

Use file-driven SQL migration runner and regression tests where possible.

When working with external API's use the list below for the API Documentation for the respective systems:
SyncroRMM
https://api-docs.syncromsp.com/
TacticalRMM:
https://api.hawkinsitsolutions.com.au/api/schema/swagger-ui/

Test all code changes to attempt to prevent Internal Server Error occurrences.

create a .env.exmaple file that includes all available environment variables, this should be updated automatically as new variables are added.

If moving items between locations, match the style of the destination rather than the source.

For tables always keep row dividers  heights consistent across the width of the table.

Do not generate binary files.

Python bytecode cache files should not be committed to the repository.
