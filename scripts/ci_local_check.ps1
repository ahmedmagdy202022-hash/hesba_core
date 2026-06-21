$ErrorActionPreference = "Stop"

python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
