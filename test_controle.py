"""Essais sans réseau du contrôle de vent.yml (controle.py) et de l'étape qui le lance."""
import datetime as dt
import json
import os
import subprocess

import pytest

import controle

yaml = pytest.importorskip("yaml")  # PyYAML : pip install pyyaml (essais seulement)

NOW = dt.datetime(2026, 11, 22, 10, 7, tzinfo=dt.timezone.utc)
LATEST = {"completed": True, "reference_time": "2026-11-22T06:00:00Z"}
WORKFLOW = os.path.join(os.path.dirname(__file__), ".github", "workflows", "vent.yml")


def index(run, received="2026-11-22T09:31Z"):
    return {"run": run, "received": received}


def test_the_online_run_is_the_oldest_of_the_indexes():
    assert controle.oldest_run([index("2026-11-22T06:00Z"), index("2026-11-22T00:00Z")]) == "2026-11-22T00:00Z"
    assert controle.oldest_run([index("2026-11-22T06:00Z")]) == "2026-11-22T06:00Z"


def test_a_missing_or_broken_index_counts_as_none():
    assert controle.oldest_run([index("2026-11-22T06:00Z"), None]) == "aucun"
    assert controle.oldest_run([index("2026-11-22T06:00Z"), {"run": 3}]) == "aucun"
    assert controle.oldest_run([]) == "aucun"


def test_a_new_complete_run_is_published():
    publish, seen, calcul, online = controle.decide(LATEST, [index("2026-11-22T00:00Z")], False, NOW)
    assert (publish, seen, calcul, online) == (True, "2026-11-22T10:07Z", "2026-11-22T06:00Z", "2026-11-22T00:00Z")


def test_a_run_online_everywhere_is_not_published_again():
    publish, *_ = controle.decide(LATEST, [index("2026-11-22T06:00Z"), index("2026-11-22T06:00Z")], False, NOW)
    assert publish is False


def test_a_late_host_is_caught_up():
    """deploy-pages raté, Cloudflare à jour : Pages, que lit l'app installée, est rattrapé au passage suivant."""
    publish, *_ = controle.decide(LATEST, [index("2026-11-22T00:00Z"), index("2026-11-22T06:00Z")], False, NOW)
    assert publish is True


def test_an_incomplete_run_is_not_published():
    publish, *_ = controle.decide({**LATEST, "completed": False}, [index("2026-11-22T00:00Z")], False, NOW)
    assert publish is False


def test_a_forced_pass_keeps_the_received_time_of_the_run_already_online():
    """Passage relancé pour CAMS : ICON est déjà en ligne, son "received" ne recule pas à l'heure du passage."""
    publish, seen, *_ = controle.decide(LATEST, [index("2026-11-22T06:00Z", "2026-11-22T09:31Z")], True, NOW)
    assert (publish, seen) == (True, "2026-11-22T09:31Z")


def test_a_forced_pass_of_a_new_run_takes_the_current_time():
    publish, seen, *_ = controle.decide(LATEST, [index("2026-11-22T00:00Z")], True, NOW)
    assert (publish, seen) == (True, "2026-11-22T10:07Z")


def test_main_writes_the_outputs_and_reads_every_index(tmp_path, monkeypatch):
    output = tmp_path / "sortie"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    pages, cloudflare = "https://pages.test/index.json", "https://mawsim-index.essai.workers.dev/index.json"
    read_urls = []

    def read(url):
        read_urls.append(url)
        if url.endswith("latest.json"):
            return LATEST
        if url == pages:
            return index("2026-11-22T00:00Z")
        raise OSError("injoignable")  # Cloudflare ne répond pas : "aucun"

    assert controle.main(["--source", "https://source.test", "--modele", "dwd_icon", "--index", pages, "--index", cloudflare],
                         read=read, now=NOW) == 0
    assert read_urls == ["https://source.test/dwd_icon/latest.json", pages, cloudflare]
    assert output.read_text(encoding="utf-8") == "publier=oui\nvu=2026-11-22T10:07Z\n"


def test_an_unreadable_latest_fails_the_step():
    def read(url):
        raise OSError("Open-Meteo injoignable")
    with pytest.raises(OSError):
        controle.main(["--source", "https://source.test", "--modele", "dwd_icon", "--index", "https://pages.test/index.json"], read=read)


def test_reading_sends_a_user_agent(monkeypatch):
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, *args):
            return json.dumps({"run": "2026-11-22T06:00Z"}).encode()

    def urlopen(request, timeout):
        seen.update(url=request.full_url, agent=request.get_header("User-agent"), timeout=timeout)
        return Response()

    monkeypatch.setattr(controle.urllib.request, "urlopen", urlopen)
    assert controle.read_json("https://pages.test/index.json") == {"run": "2026-11-22T06:00Z"}
    assert seen == {"url": "https://pages.test/index.json", "agent": "mawsim-atelier", "timeout": 30}


# --- L'étape de vent.yml elle-même : lancée par bash avec un faux python3 qui note ses arguments.

def workflow():
    with open(WORKFLOW, encoding="utf-8") as source:
        return yaml.safe_load(source)


def check_step():
    steps = workflow()["jobs"]["preparer"]["steps"]
    return next(step for step in steps if step.get("id") == "nouveau")


def bash():
    for candidate in (r"C:\Program Files\Git\bin\bash.exe", "/bin/bash", "/usr/bin/bash"):
        if os.path.isfile(candidate):
            return candidate
    return None


def run_check_step(tmp_path, sous_domaine, forcer="false"):
    shell = bash()
    if not shell:
        pytest.skip("bash introuvable")
    shim = tmp_path / "bin"
    shim.mkdir(exist_ok=True)
    noted = tmp_path / "arguments.txt"
    (shim / "python3").write_text(f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > "{noted.as_posix()}"\n',
                                  encoding="utf-8", newline="\n")
    os.chmod(shim / "python3", 0o755)
    script = tmp_path / "etape.sh"
    script.write_text(check_step()["run"], encoding="utf-8", newline="\n")
    env = {**os.environ, "SOURCE": "https://source.test", "MODELE": "dwd_icon", "SOUS_DOMAINE": sous_domaine, "FORCER": forcer,
           "GITHUB_REPOSITORY_OWNER": "proprietaire", "GITHUB_REPOSITORY": "proprietaire/depot"}
    shim_path = subprocess.run([shell, "-c", f"cygpath -u '{shim}' 2>/dev/null || echo '{shim.as_posix()}'"],
                               capture_output=True, text=True).stdout.strip()
    result = subprocess.run([shell, "-c", f'PATH="{shim_path}:$PATH"; . "{script.as_posix()}"'], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return noted.read_text(encoding="utf-8").split("\n")[:-1], result.stdout


def test_without_the_switch_only_pages_is_read(tmp_path):
    """Sans la variable CLOUDFLARE_ACTIF (SOUS_DOMAINE vide), le contrôle ne lit que Pages, comme avant."""
    arguments, output = run_check_step(tmp_path, "")
    assert arguments == ["controle.py", "--source", "https://source.test", "--modele", "dwd_icon",
                         "--index", "https://proprietaire.github.io/depot/index.json"]
    assert "add-mask" not in output


def test_with_the_switch_both_indexes_are_read_and_the_label_masked(tmp_path):
    arguments, output = run_check_step(tmp_path, "secret.workers.dev", forcer="true")
    assert arguments == ["controle.py", "--source", "https://source.test", "--modele", "dwd_icon",
                         "--index", "https://proprietaire.github.io/depot/index.json",
                         "--index", "https://mawsim-index.secret.workers.dev/index.json", "--forcer"]
    assert "::add-mask::secret" in output


def test_the_switch_and_the_secret_feed_the_step():
    env = check_step()["env"]
    assert env["SOUS_DOMAINE"] == "${{ vars.CLOUDFLARE_ACTIF == 'oui' && secrets.CLOUDFLARE_SOUS_DOMAINE || '' }}"
    assert env["FORCER"] == "${{ inputs.forcer }}"  # par l'environnement, jamais collé dans le script


def test_the_cloudflare_job_is_off_without_the_switch_and_holds_no_credentials():
    jobs = workflow()["jobs"]
    job = jobs["publier-cloudflare"]
    assert job["if"] == "needs.preparer.outputs.publier == 'oui' && vars.CLOUDFLARE_ACTIF == 'oui'"
    assert job["permissions"] == {"contents": "read"}
    checkout = next(step for step in job["steps"] if str(step.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["persist-credentials"] is False
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    assert "npm ci --prefix cloudflare" in runs and "npx" not in runs
    assert "::add-mask::" in runs
    # Pages ne dépend pas de Cloudflare, ni l'inverse.
    assert jobs["publier-pages"]["needs"] == "preparer" and "CLOUDFLARE" not in json.dumps(jobs["publier-pages"])


def test_wrangler_is_locked():
    folder = os.path.join(os.path.dirname(__file__), "cloudflare")
    with open(os.path.join(folder, "package.json"), encoding="utf-8") as source:
        package = json.load(source)
    with open(os.path.join(folder, "package-lock.json"), encoding="utf-8") as source:
        lock = json.load(source)
    version = package["devDependencies"]["wrangler"]
    assert version[0].isdigit()  # une version exacte, pas un intervalle
    assert lock["packages"]["node_modules/wrangler"]["version"] == version
    assert all("integrity" in entry for name, entry in lock["packages"].items() if name and not entry.get("link"))


def test_the_timer_watches_the_model_of_the_workflow():
    import tomllib
    with open(os.path.join(os.path.dirname(__file__), "cloudflare", "minuteur", "wrangler.toml"), "rb") as source:
        timer = tomllib.load(source)
    assert timer["vars"]["MODELE"] == workflow()["env"]["MODELE"]
    assert timer["workers_dev"] is False and timer["triggers"]["crons"] == ["*/5 * * * *"]
    assert timer["services"] == [{"binding": "INDEX", "service": "mawsim-index"}]
