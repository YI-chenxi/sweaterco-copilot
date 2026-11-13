# Account identity

`AuthService` registers accounts and verifies credentials. It returns a
canonical user ID and username after authentication; session creation and
workspace authorization happen in the account backend.

The PostgreSQL adapters use the `account_users` and `account_login_attempts`
tables created by the account storage migrations. Usernames are normalized
for lookup. Passwords are 6–15 characters, stored as Argon2id hashes, and
never written to logs or ordinary Streamlit session state. A login throttle
limits attempts per normalized username and requires a stable server secret.

Credential failures use a generic public error so the UI does not reveal
whether an account exists. The module does not issue browser tokens or cookies.

Run the focused tests with:

```powershell
python -m pytest -q tests/accounts/identity
```
