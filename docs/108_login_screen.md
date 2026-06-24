# 108 Login Screen

## Goal

Start the product flow from a protected login screen instead of building random UI pages.

## Implemented

- New clean branch from main: `108-login-screen`.
- Added `/login/` route with a custom Hesba login page.
- Added `/logout/` route.
- Login redirects authenticated users to `/dashboard/`.
- Home, dashboard, reports, and status views require authentication.
- Login page uses Hesba visual identity: navy, teal, gold, Arabic RTL, and mobile-friendly layout.

## Protected flow

Unauthenticated users should be redirected to:

`/login/?next=<requested-page>`

After login, users go to:

`/dashboard/`

## Next checkpoint

`109_APP_SHELL_NAVIGATION`
