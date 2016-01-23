# asylum

Membership management for hacklabs. uses Python 3.4.

Uses django-environ for configurations, create `.env`-file in your project dir to override settings.

## Install/setup

### General

Fork the repo on github and use you local fork as checkout source, you will want to add your own modules
with custom callbacks for automating things like mailing list subscriptions for new members

For Ubuntu 14.04 LTS

  - Add the original repo as upstream `git remote add upstream https://github.com/hacklab-fi/asylum.git`
  - Make a branch for your local changes `git checkout -b myhackerspace`
  - Install nodejs v4 first (needs PPA and key and stuff, nodesource has a handy script for this)

<pre><code>sudo apt-get install curl
curl -sL https://deb.nodesource.com/setup_4.x | sudo -E bash -
sudo apt-get install -y nodejs</code></pre>

  - `xargs -a <(awk '/^\s*[^#]/' "requirements.apt") -r -- sudo apt-get install` Installs all packages listed in requirements.apt
  - `sudo pip install maildump` currently not python3 compatible due to broken package, only needed if you're going to run in development mode
  - `` virtualenv -p `which python3.4` venv && source venv/bin/activate ``
    - Note: this might not work. If it doesn't, try `virtualenv-3.4`.
      If you don't have `virtualenv-3.4`, you might need to install it (`sudo pip3.4 install virtualenv`).
      If the installation command fails, you'll have to bootstrap pip for your Python3.4 installation (`wget https://bootstrap.pypa.io/get-pip.py && sudo python3.4 get-pip.py`).
      Good luck.
  - `pip install -r requirements/local.txt` (or `pip install -r requirements/production.txt` if installing on production)
  - Create the postgres database
    - `sudo apt-get install postgresql-9.3` this is not in requirements.apt since you might want to use a dedicated postgres host.
    - `sudo su - postgres`
    - `createuser asylum && createdb -E utf-8 -T template0 -O asylum asylum && psql -U postgres -d postgres -c "alter user asylum with password 'asylum';"`
      - Change at least the password,in createdb `-O asylum` is the user that owns the database.
  - `./manage.py migrate`
  - `find . -name '._*' | xargs rm ; for app in $( find . -path '*/locale' ); do (cd $(dirname $app) && ../manage.py compilemessages ); done`
  - `./manage.py createinitialrevisions`
  - `./manage.py createsuperuser`
  - `npm run build`

### Production setup

  - Create a `.env` file in the project directory before running any of the manage.py commands above. See the example file, you need at least the following variables
      - DJANGO_SETTINGS_MODULE (=config.settings.production)
      - DJANGO_SENTRY_DSN
        - https://hub.docker.com/_/sentry/
        - https://docs.getsentry.com/hosted/
      - DATABASE_URL (=postgres://pguser:pgpassword@localhost/dbname)
  - `./manage.py collectstatic --noinput`
  - Setup uWSGI
    - `sudo apt-get install uwsgi-plugin-python3 uwsgi`
    - `nano -w /etc/uwsgi/apps-available/asylum.ini` (see below)
    - `ln -s /etc/uwsgi/apps-available/asylum.ini /etc/uwsgi/apps-enabled/asylum.ini`
    - `service uwsgi reload`
  - Setup Nginx
    - TODO: instructions
  - Configure backups
    - TODO: instructions

#### uWSGI config example

<pre><code>[uwsgi]
vhost = true
plugins = python3
# You could also use the unix socket but we use the http-one
http-socket = 127.0.0.1:9001
master = true
enable-threads = true
processes = 2
wsgi-file = /home/myhackerspace/asylum/project/config/wsgi.py
virtualenv = /home/myhackerspace/asylum/project/venv
chdir = /home/myhackerspace/asylum/project
touch-reload = /home/myhackerspace/asylum/project/reload
env = DJANGO_SETTINGS_MODULE=config.settings.production</code></pre>


### Updating upstream changes

In the `project` dir of your checkout

    git checkout master
    git fetch upstream
    git rebase upstream/master master
    git checkout myhackerspace
    git rebase master
    source venv/bin/activate
    pip install -r requirements/production.txt
    ./manage.py migrate
    npm run build
    ./manage.py collectstatic --noinput
    for app in locale */locale; do (cd $(dirname $app) && ../manage.py compilemessages ); done

And assuming you have uWSGI configured `touch reload`

## Cron jobs

Until we maybe decide on Celery for running various (timed or otherwise) tasks add the following to your crontab:

    SHELL=/bin/bash
    @daily      cd /path/to/project ; source venv/bin/activate ; ./manage.py addrecurring
    @daily      cd /path/to/project ; set -o allexport ; source .env; set +o allexport ; pg_dump -c $DATABASE_URL | gzip >database_backup.sql.gz

## Running in development mode

  - `maildump --http-ip 0.0.0.0 -p ~/maildump.pid` (maybe needs sudo)
    - Web interface at <http://localhost:1080/> (replace localhost with your vagrant ip)
  - `source venv/bin/activate`
  - `npm run watch &` If you want to develop the JS/LESS stuff this will autocompile them on change
  - `./manage.py runserver 0.0.0.0:8000`
  - `maildump -p ~/maildump.pid --stop`
  - Make localizations: `find . -name '._*' | xargs rm ; for app in $( find . -path '*/locale' ); do (cd $(dirname $app) && ../manage.py makemessages ); done`
    - If you add your own apps, make sure to create the `locale` directory for them too.

If you need the special environment variables in scripts not run via manage.py, use `set -o allexport ; source .env; set +o allexport` to load them.

## Backups and direct database access

See the cronjobs above for a nightly database dump. As for manual dump or restore start with  `set -o allexport ; source .env; set +o allexport` to load the environment.

For a manual dump run ```pg_dump -c $DATABASE_URL | gzip >database_backup_`date +%Y%m%d_%H%M`.sql.gz```.

For restore run ```zcat database_backup.sql.gz | psql $DATABASE_URL``` (you might need to drop and recreate the database first, see the setup instructions for creating)
