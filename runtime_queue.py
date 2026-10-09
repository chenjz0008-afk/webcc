"""Procrastinate queue; only task IDs cross the worker boundary."""
import http.client
import json
import os
import procrastinate


def build_app(connector):
    app = procrastinate.App(connector=connector)

    @app.task(name='webcc.runtime', queue='webcc-runtime', retry=False)
    def process(identity):
        connection = http.client.HTTPConnection('127.0.0.1', int(os.environ.get('MANAGER_PORT', '9000')), timeout=240)
        try:
            from runtime_api import internal_token
            connection.request('POST', '/internal/runtime', json.dumps({'id': identity}),
                               {'Authorization': 'Bearer ' + os.environ['CLEWDR_ADMIN_PASSWORD'], 'Content-Type': 'application/json',
                                'X-WebCC-Internal': internal_token(os.environ['CLEWDR_ADMIN_PASSWORD'])})
            response = connection.getresponse()
            response.read(8192)
            if response.status != 200:
                raise RuntimeError('Runtime dispatch failed: HTTP ' + str(response.status))
        finally:
            connection.close()

    @app.periodic(cron='* * * * *')
    @app.task(name='webcc.recover', queue='webcc-runtime', queueing_lock='webcc-recover', retry=False)
    async def recover(timestamp):
        stalled = await app.job_manager.get_stalled_jobs(seconds_since_heartbeat=90)
        for job in stalled:
            if job.task_name == 'webcc.runtime':
                await app.job_manager.retry_job(job)
    return app, process


def enqueue(database, identity, delay=0):
    app, task = build_app(procrastinate.SyncPsycopgConnector())
    task.configure(connection=database, schedule_in={'seconds': delay}).defer(identity=identity)


def main():
    os.umask(0o077)
    app, _ = build_app(procrastinate.PsycopgConnector(conninfo=os.environ['MANAGER_DATABASE_URL'], min_size=1, max_size=2))
    with app.open():
        app.run_worker(queues=['webcc-runtime'], concurrency=1)


if __name__ == '__main__':
    main()
