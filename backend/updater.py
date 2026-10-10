import http.client
import json
import shutil
import time
import urllib.request
from pathlib import Path

from manager import Problem, StreamState, private_write


REPOSITORY = "https://api.github.com/repos/Xerxes-2/clewdr"
IMAGE = "ghcr.io/xerxes-2/clewdr"


def github(path):
    request = urllib.request.Request(REPOSITORY + path, headers={"User-Agent": "clewdr-manager", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def request_worker(port, key, method, path, payload=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    try:
        body = None if payload is None else json.dumps(payload)
        connection.request(method, path, body, {"Authorization": "Bearer " + key,
                           "Content-Type": "application/json", "Accept-Encoding": "identity"})
        response = connection.getresponse()
        data = response.read(1024 * 1024)
        if response.status != 200:
            raise Problem(502, "候选版本验证失败，HTTP " + str(response.status))
        return data
    finally:
        connection.close()


def validate(port, key, generation=False):
    models = json.loads(request_worker(port, key, "GET", "/v1/models"))
    if not models.get("data"):
        raise Problem(502, "候选版本模型接口不兼容")
    if generation:
        payload = {"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "Reply with OK."}], "max_tokens": 16, "stream": False}
        value = json.loads(request_worker(port, key, "POST", "/v1/chat/completions", payload))
        if not value["choices"][0]["message"]["content"]:
            raise Problem(502, "候选版本没有生成内容")
        payload["stream"] = True
        stream = request_worker(port, key, "POST", "/v1/chat/completions", payload)
        state = StreamState()
        state.feed(stream)
        if not state.openai_chunk or state.failed:
            raise Problem(502, "候选版本流式接口不兼容")


def remove(manager, name):
    if manager.inspect(name)["status"] != "missing":
        manager.docker("stop", name)
        manager.docker("rm", name)


def replace_worker(manager, account, image):
    old_image = account["image"]
    config = Path(account["directory"]) / "clewdr.toml"
    backup = manager.data / "backups" / (account["id"] + "-" + str(time.time_ns()) + ".toml")
    private_write(backup, config.read_text())
    from worker_profiles import stop
    stop(manager, account)
    remove(manager, account["container"])
    try:
        manager.run_worker(account, image=image)
        version = manager.wait_ready(account["port"])
        validate(account["port"], account["key"])
        account.update(image=image, version=version)
        manager.save()
    except Exception:
        try:
            remove(manager, account["container"])
            private_write(config, backup.read_text())
            manager.run_worker(account, image=old_image)
            account["version"] = manager.wait_ready(account["port"])
            account["image"] = old_image
            manager.save()
        except Exception:
            manager.set_status(account, "error", "升级和回退失败，需人工检查")
        raise


def check(manager, follow, auto):
    if not manager.operations.acquire(blocking=False):
        return {"status": "busy"}
    selected = None
    candidate_name = None
    candidate_dir = None
    try:
        update = manager.state["update"]
        update.update(status="checking", checked_at=int(time.time()), follow=follow, message="正在检测官方版本")
        manager.save()
        if follow == "master":
            commit = github("/commits/master")
            sha = commit["sha"]
            reference = "sha-" + sha[:7]
            update.update(latest_sha=sha, latest_ref=reference)
            runs = github("/actions/runs?head_sha=" + sha + "&event=push&per_page=10")["workflow_runs"]
            if not any(run.get("name") == "build" and run.get("head_sha") == sha and run.get("conclusion") == "success" for run in runs):
                update.update(status="waiting_build", message="等待作者的官方构建通过")
                manager.save()
                return dict(update)
        elif follow == "release":
            release = github("/releases/latest")
            reference = release["tag_name"]
            sha = github("/commits/" + reference)["sha"]
            update.update(latest_sha=sha, latest_ref=reference)
        else:
            raise Problem(400, "更新模式只能是 master 或 release")
        ready = [a for a in manager.state["accounts"].values() if manager.available(a)]
        if not auto:
            update.update(status="available", message="仅检测，自动升级已关闭")
            manager.save()
            return dict(update)
        if not ready:
            update.update(status="waiting_account", message="需要至少一个正常账号验证候选版本")
            manager.save()
            return dict(update)
        if update.get("deployed_sha") == sha and all(a["image"] == update.get("image") for a in ready):
            update.update(status="current", message="当前正常账号已使用已验证的最新提交")
            manager.save()
            return dict(update)
        update.update(status="pulling", message="正在下载官方提交镜像")
        manager.save()
        target = IMAGE + ":" + reference
        manager.docker("pull", target, timeout=300)
        digest = json.loads(manager.docker("image", "inspect", target))[0]["RepoDigests"][0]
        if not digest.startswith(IMAGE + "@sha256:"):
            raise Problem(502, "镜像摘要不符合官方仓库")
        if update.get("approved_sha") != sha:
            selected = ready[0]
            manager.set_status(selected, "updating", "候选版本验证中，暂不分配新请求")
            manager.wait_idle(selected)
            account = manager.acquire(selected["id"], statuses=("updating",))
            try:
                candidate_dir = manager.data / "candidates" / (selected["id"] + "-" + str(time.time_ns()))
                candidate_dir.mkdir(parents=True, mode=0o700)
                candidate_dir.chmod(0o700)
                private_write(candidate_dir / "clewdr.toml", (Path(selected["directory"]) / "clewdr.toml").read_text())
                candidate_name = "clewdr-check-" + selected["id"]
                port = manager.free_port()
                manager.run_worker(selected, image=digest, directory=str(candidate_dir), port=port, name=candidate_name)
                manager.wait_ready(port)
                validate(port, selected["key"], generation=True)
            finally:
                manager.release(account)
            remove(manager, candidate_name)
            candidate_name = None
            shutil.rmtree(candidate_dir)
            candidate_dir = None
            manager.set_status(selected, "ready")
            selected = None
            update.update(approved_sha=sha, image=digest)
            manager.image = digest
            manager.save()
        update.update(status="rolling", message="候选验证通过，逐个升级正常账号")
        manager.save()
        for account in ready:
            if account["image"] == digest:
                continue
            selected = account
            manager.set_status(account, "updating", "等待现有请求结束")
            manager.wait_idle(account)
            replace_worker(manager, account, digest)
            manager.set_status(account, "ready")
            selected = None
        update.update(status="current", deployed_sha=sha, deployed_ref=reference, image=digest,
                      deployed_at=int(time.time()), message="最新提交已验证并部署；暂停/隔离账号保持原状态")
        manager.image = digest
        manager.save()
        return dict(update)
    except Exception as error:
        if selected is not None and selected["status"] == "updating":
            manager.set_status(selected, "ready", "更新延期或失败，保留原版本")
        manager.state["update"].update(status="failed", message=str(error) if isinstance(error, Problem) else "版本检测或验证失败：" + type(error).__name__)
        manager.save()
        return dict(manager.state["update"])
    finally:
        if candidate_name:
            try:
                remove(manager, candidate_name)
            except Exception:
                manager.state["update"].update(status="failed", message="候选清理失败，需人工检查")
                manager.save()
        if candidate_dir:
            shutil.rmtree(candidate_dir, ignore_errors=True)
        manager.operations.release()
