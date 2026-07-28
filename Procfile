web: gunicorn --bind 0.0.0.0:$PORT --worker-class gthread --workers 1 --threads 8 --timeout 120 --access-logfile - --error-logfile - server:app
