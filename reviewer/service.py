"""SQLite-backed review state, managed artifacts, and serial import processing."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import threading
from datetime import datetime, timezone
import uuid

from . import comparison as cmp


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Service:
    def __init__(self, directory=None, start_worker=True):
        self.directory = Path(directory or cmp.ROOT / "data/reviewer").resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        for name in ("sources", "conversions", "snapshots", "backups"):
            (self.directory / name).mkdir(exist_ok=True)
        self.database = self.directory / "reviewer.sqlite3"
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="schematic-review") if start_worker else None
        self.migrate()
        with self.db() as db:
            db.execute("UPDATE jobs SET status='interrupted', error='Service stopped during processing; retry this job' WHERE status='running'")
            queued = [row[0] for row in db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created")]
        for job in queued:
            self.schedule(job)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def migrate(self):
        with self.db() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise ValueError("Reviewer database belongs to a newer application version")
            if version == 0:
                if db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                    with sqlite3.connect(self.directory / "backups" / f"before-v1-{uid()}.sqlite3") as backup:
                        db.backup(backup)
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS builds (
                        id TEXT PRIMARY KEY, source_hash TEXT UNIQUE NOT NULL,
                        filename TEXT NOT NULL, document TEXT NOT NULL, created TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS conversions (
                        id TEXT PRIMARY KEY, build_id TEXT NOT NULL REFERENCES builds(id),
                        source_hash TEXT NOT NULL, artifact_hash TEXT NOT NULL,
                        fingerprint TEXT NOT NULL, report TEXT NOT NULL, created TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS reviews (
                        id TEXT PRIMARY KEY, build_id TEXT NOT NULL REFERENCES builds(id),
                        conversion_id TEXT NOT NULL REFERENCES conversions(id), decision TEXT NOT NULL,
                        fingerprint TEXT NOT NULL, scope TEXT NOT NULL, notes TEXT NOT NULL, created TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS jobs (
                        id TEXT PRIMARY KEY, build_id TEXT NOT NULL REFERENCES builds(id),
                        status TEXT NOT NULL, error TEXT, created TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS datasets (
                        id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                        document TEXT NOT NULL, created TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS membership (
                        dataset_id TEXT NOT NULL REFERENCES datasets(id),
                        build_id TEXT NOT NULL REFERENCES builds(id), PRIMARY KEY(dataset_id,build_id));
                    CREATE TABLE IF NOT EXISTS splits (
                        dataset_id TEXT NOT NULL REFERENCES datasets(id), family TEXT NOT NULL,
                        split TEXT NOT NULL, PRIMARY KEY(dataset_id,family));
                    CREATE TABLE IF NOT EXISTS versions (
                        id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES datasets(id),
                        manifest TEXT NOT NULL, created TEXT NOT NULL);
                    PRAGMA user_version=1;
                """)
                version = 1
            if version < 2:
                db.execute("CREATE TABLE IF NOT EXISTS labels (category TEXT NOT NULL, label TEXT NOT NULL COLLATE NOCASE, PRIMARY KEY(category,label))")
                defaults = {
                    "styles": ["medieval", "modern", "fantasy"],
                    "functions": ["residential", "agricultural", "decorative", "technical"],
                    "features": ["interior", "multiple floors"],
                }
                for row in db.execute("SELECT document FROM builds"):
                    document = json.loads(row[0])
                    for category in defaults:
                        defaults[category].extend(document.get(category, []))
                for category, values in defaults.items():
                    db.executemany("INSERT OR IGNORE INTO labels(category,label) VALUES (?,?)", [(category, v.strip()) for v in values if isinstance(v, str) and v.strip()])
                canonical = {category: {row[0].casefold(): row[0] for row in db.execute("SELECT label FROM labels WHERE category=?", (category,))}
                             for category in defaults}
                for row in db.execute("SELECT id,document FROM builds"):
                    document = json.loads(row[1])
                    changed = False
                    for category, labels in canonical.items():
                        values = [labels.get(value.strip().casefold(), value.strip()) for value in document.get(category, []) if isinstance(value, str) and value.strip()]
                        if values != document.get(category, []):
                            document[category] = list(dict.fromkeys(values))
                            changed = True
                    if changed:
                        db.execute("UPDATE builds SET document=? WHERE id=?", (dump(document), row[0]))
                db.execute("PRAGMA user_version=2")

    def close(self):
        if self.executor:
            self.executor.shutdown(wait=True)

    def schedule(self, job):
        if self.executor:
            self.executor.submit(self.process, job)

    def label_options(self):
        with self.db() as db:
            return {category: [row[0] for row in db.execute("SELECT label FROM labels WHERE category=? ORDER BY label COLLATE NOCASE", (category,))]
                    for category in ("styles", "functions", "features")}

    def add_label(self, category, label):
        if category not in ("styles", "functions", "features") or not isinstance(label, str):
            raise ValueError("Choose a label category and text")
        label = " ".join(label.split())
        if not label or len(label) > 100 or "," in label:
            raise ValueError("Label must be 1–100 characters and cannot contain a comma")
        with self.db() as db:
            existing = db.execute("SELECT label FROM labels WHERE category=? AND label=? COLLATE NOCASE", (category, label)).fetchone()
            if existing:
                return {"category": category, "label": existing[0], "created": False}
            db.execute("INSERT INTO labels(category,label) VALUES (?,?)", (category, label))
        return {"category": category, "label": label, "created": True}

    def import_bytes(self, filename, content, reference=None):
        filename = Path(filename.replace("\\", "/")).name
        if not filename.lower().endswith(".litematic"):
            raise ValueError("Only .litematic files are supported in this release")
        if len(content) > cmp.MAX_FILE_BYTES:
            raise ValueError("File exceeds the 64 MiB import limit")
        digest = hashlib.sha256(content).hexdigest()
        with self.lock, self.db() as db:
            existing = db.execute("SELECT * FROM builds WHERE source_hash=?", (digest,)).fetchone()
            ref = {"filename": filename, "reference": reference, "imported": now()}
            if existing:
                doc = json.loads(existing["document"])
                if not any(r["filename"] == filename and r["reference"] == reference for r in doc["source_references"]):
                    doc["source_references"].append(ref)
                    db.execute("UPDATE builds SET document=? WHERE id=?", (dump(doc), existing["id"]))
                return {"build_id": existing["id"], "duplicate": True}
            source = self.directory / "sources" / f"{digest}.litematic"
            if not source.exists():
                with source.open("xb") as handle:
                    handle.write(content)
            elif cmp.digest(source) != digest:
                raise ValueError("Managed source hash collision or altered source; import stopped")
            build, job = uid(), uid()
            doc = {"type": "unclassified", "styles": [], "functions": [], "features": [],
                   "redstone": "unknown", "notes": "", "family": digest, "family_confirmed": False,
                   "archived": False, "provenance": "unknown", "evidence": "", "source_url": "",
                   "revision": "", "license": "", "attribution": "", "source_references": [ref],
                   "classification_origin": "human", "suggestions": [], "summary": None}
            db.execute("INSERT INTO builds VALUES (?,?,?,?,?)", (build, digest, filename, dump(doc), now()))
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?)", (job, build, "queued", None, now()))
        self.schedule(job)
        return {"build_id": build, "job_id": job, "duplicate": False}

    def process(self, job):
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
            if not row or row["status"] != "queued":
                return
            db.execute("UPDATE jobs SET status='running', error=NULL WHERE id=?", (job,))
        try:
            build = self.get_build(row["build_id"])
            source = self.directory / "sources" / f"{build['source_hash']}.litematic"
            if cmp.digest(source) != build["source_hash"]:
                raise ValueError("Source file changed since import")
            original = cmp.load(source)
            conv = uid()
            output = self.directory / "conversions" / f"{conv}.litematic"
            reused = False
            # Reuse only hash-matched source artifacts, always inspect actual files again.
            for archive in (cmp.ROOT / "exports").glob("*/building.internal.json"):
                info = json.loads(archive.read_text(encoding="utf-8"))
                if info.get("source_sha256") != build["source_hash"]:
                    continue
                candidates = list(archive.parent.glob("*-roundtrip.litematic"))
                if len(candidates) == 1:
                    shutil.copyfile(candidates[0], output)
                    reused = True
                    break
            if not reused:
                cmp.pipeline.write_build(original, output)
            converted = cmp.load(output)
            self.commit_conversion(build["id"], conv, original, converted, output)
            with self.lock, self.db() as db:
                current = json.loads(db.execute("SELECT document FROM builds WHERE id=?", (build["id"],)).fetchone()[0])
                current["summary"] = cmp.pipeline.summary(original)
                db.execute("UPDATE builds SET document=? WHERE id=?", (dump(current), build["id"]))
                db.execute("UPDATE jobs SET status='completed', error=NULL WHERE id=?", (job,))
        except Exception as error:
            with self.db() as db:
                db.execute("UPDATE jobs SET status='failed', error=? WHERE id=?", (f"{type(error).__name__}: {error}", job))

    def commit_conversion(self, build_id, conversion_id, original, converted, output):
        report = cmp.compare(original, converted)
        independent = cmp.pipeline.cross_check(output, converted)
        report["independent_reader"] = independent
        report["passed"] = report["passed"] and independent["status"] == "passed"
        artifact = self.directory / "conversions" / f"{conversion_id}.json"
        with artifact.open("x", encoding="utf-8") as handle:
            json.dump(report, handle)
        brief = {k: v for k, v in report.items() if k not in ("original", "converted", "markers")}
        with self.lock, self.db() as db:
            db.execute("INSERT INTO conversions VALUES (?,?,?,?,?,?,?)", (
                conversion_id, build_id, original["source_sha256"], cmp.digest(output), cmp.fingerprint(), dump(brief), now()))

    def replacement(self, build_id, filename, content):
        build = self.get_build(build_id)
        if not filename.lower().endswith(".litematic") or len(content) > cmp.MAX_FILE_BYTES:
            raise ValueError("Replacement must be a .litematic file no larger than 64 MiB")
        source = self.directory / "sources" / f"{build['source_hash']}.litematic"
        original = cmp.load(source)
        if original["source_sha256"] != build["source_hash"]:
            raise ValueError("Managed original has changed")
        conversion_id = uid()
        output = self.directory / "conversions" / f"{conversion_id}.litematic"
        with output.open("xb") as handle:
            handle.write(content)
        converted = cmp.load(output)
        self.commit_conversion(build_id, conversion_id, original, converted, output)
        return self.get_build(build_id)

    def jobs(self):
        with self.db() as db:
            return [dict(row) for row in db.execute("SELECT j.*,b.filename FROM jobs j JOIN builds b ON j.build_id=b.id ORDER BY j.created DESC")]

    def retry(self, job_id):
        with self.lock, self.db() as db:
            job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                raise KeyError("Job not found")
            if job["status"] not in ("failed", "interrupted"):
                raise ValueError("Only failed or interrupted jobs can be retried")
            db.execute("UPDATE jobs SET status='queued',error=NULL WHERE id=?", (job_id,))
        self.schedule(job_id)
        return {"job_id": job_id}

    def get_build(self, build_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM builds WHERE id=?", (build_id,)).fetchone()
            if not row:
                raise KeyError("Build not found")
            result = dict(row)
            result.update(json.loads(result.pop("document")))
            conv = db.execute("SELECT * FROM conversions WHERE build_id=? ORDER BY created DESC LIMIT 1", (build_id,)).fetchone()
            reviews = [dict(r) for r in db.execute("SELECT * FROM reviews WHERE build_id=? ORDER BY created DESC", (build_id,))]
            result["reviews"] = reviews
            result["conversion"] = None
            result["review_status"] = "pending"
            result["stale"] = False
            if conv:
                conversion = dict(conv)
                conversion["report"] = json.loads(conversion["report"])
                result["conversion"] = conversion
                source = self.directory / "sources" / f"{result['source_hash']}.litematic"
                output = self.directory / "conversions" / f"{conv['id']}.litematic"
                result["stale"] = (conv["fingerprint"] != cmp.fingerprint() or not source.exists() or
                    not output.exists() or cmp.digest(source) != result["source_hash"] or
                    cmp.digest(output) != conv["artifact_hash"])
                if reviews and reviews[0]["conversion_id"] == conv["id"] and not result["stale"]:
                    result["review_status"] = reviews[0]["decision"]
            result["eligibility_reasons"] = self.reasons(result)
            result["eligible"] = not result["eligibility_reasons"]
            return result

    @staticmethod
    def reasons(build):
        reasons = []
        if build["archived"]:
            reasons.append("Build is archived")
        if not build["conversion"] or not build["conversion"]["report"]["passed"]:
            reasons.append("Conversion validation has not passed")
        if build["stale"]:
            reasons.append("Source, conversion, or converter changed; reprocess and review")
        if build["review_status"] != "approved":
            reasons.append("Current conversion has not been approved")
        if build["provenance"] not in ("allowed", "project_generated") or not build["evidence"].strip():
            reasons.append("Permitted usage and evidence are not recorded")
        if build["type"] == "unclassified" or build["redstone"] == "unknown":
            reasons.append("Confirm building type and redstone presence")
        return reasons

    def builds(self, query="", review="", building_type="", redstone="", provenance="", style="", function="", archived="active", min_dimension=0, max_dimension=0):
        with self.db() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM builds ORDER BY created")]
        result = []
        for build_id in ids:
            b = self.get_build(build_id)
            if archived != "all" and b["archived"] != (archived == "archived"):
                continue
            if query and query.lower() not in (b["filename"] + " " + b["notes"] + " " + " ".join(b["styles"] + b["functions"])).lower():
                continue
            if any(value and b[field] != value for field, value in (("review_status", review), ("type", building_type), ("redstone", redstone), ("provenance", provenance))):
                continue
            if style and style not in b["styles"] or function and function not in b["functions"]:
                continue
            dimensions = (b.get("summary") or {}).get("enclosing_dimensions_xyz", [])
            if (min_dimension and (not dimensions or max(dimensions) < min_dimension)) or (max_dimension and (not dimensions or max(dimensions) > max_dimension)):
                continue
            result.append(b)
        return result

    def update_build(self, build_id, changes):
        allowed = {"type", "styles", "functions", "features", "redstone", "notes", "family", "family_confirmed",
                   "archived", "provenance", "evidence", "source_url", "revision", "license", "attribution"}
        if set(changes) - allowed:
            raise ValueError("Unsupported build fields")
        for field, values in (("type", {"house", "castle", "tower", "farm", "redstone build", "other", "unclassified"}),
                              ("redstone", {"yes", "no", "unknown"}), ("provenance", {"unknown", "allowed", "denied", "project_generated"})):
            if field in changes and changes[field] not in values:
                raise ValueError(f"Invalid {field}")
        for field in ("styles", "functions", "features"):
            if field in changes:
                if not isinstance(changes[field], list) or any(not isinstance(v, str) or len(v) > 100 for v in changes[field]):
                    raise ValueError("Labels must be lists of strings of at most 100 characters")
                changes[field] = list(dict.fromkeys(v.strip() for v in changes[field] if v.strip()))
        for field in ("family_confirmed", "archived"):
            if field in changes and type(changes[field]) is not bool:
                raise ValueError(f"{field} must be a boolean")
        for field in allowed - {"styles", "functions", "features", "family_confirmed", "archived"}:
            if field in changes and (not isinstance(changes[field], str) or len(changes[field]) > 10000):
                raise ValueError(f"{field} must be text of at most 10000 characters")
        if "family" in changes and not changes["family"].strip():
            raise ValueError("Family group cannot be empty")
        with self.lock, self.db() as db:
            row = db.execute("SELECT document FROM builds WHERE id=?", (build_id,)).fetchone()
            if not row:
                raise KeyError("Build not found")
            doc = json.loads(row[0])
            for field in ("styles", "functions", "features"):
                if field in changes:
                    allowed_labels = {value[0].casefold(): value[0] for value in db.execute("SELECT label FROM labels WHERE category=?", (field,))}
                    unknown = [value for value in changes[field] if value.casefold() not in allowed_labels]
                    if unknown:
                        raise ValueError(f"Add these {field} labels to the shared list first: {', '.join(unknown)}")
                    changes[field] = list(dict.fromkeys(allowed_labels[value.casefold()] for value in changes[field]))
            if "family" in changes and changes["family"] != doc["family"]:
                changes.setdefault("family_confirmed", False)
            doc.update(changes)
            db.execute("UPDATE builds SET document=? WHERE id=?", (dump(doc), build_id))
        return self.get_build(build_id)

    def review(self, build_id, decision, notes=""):
        if decision not in ("approved", "rejected", "deferred"):
            raise ValueError("Choose approved, rejected, or deferred")
        with self.lock:
            build = self.get_build(build_id)
            conv = build["conversion"]
            if not conv or build["stale"]:
                raise ValueError("Process a current conversion before recording a review")
            if decision == "approved" and not conv["report"]["passed"]:
                raise ValueError("Resolve conversion differences before approving")
            with self.db() as db:
                db.execute("INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?)", (
                    uid(), build_id, conv["id"], decision, conv["fingerprint"], "diagnostic_data_review", notes, now()))
        return self.get_build(build_id)

    def scenes(self, build_id):
        build = self.get_build(build_id)
        if not build["conversion"]:
            raise ValueError("Conversion is not ready")
        if build["stale"]:
            raise ValueError("Comparison cache is stale; reprocess this build")
        return json.loads((self.directory / "conversions" / f"{build['conversion']['id']}.json").read_text(encoding="utf-8"))

    def reprocess(self, build_id):
        self.get_build(build_id)
        with self.lock, self.db() as db:
            if db.execute("SELECT id FROM jobs WHERE build_id=? AND status IN ('queued','running')", (build_id,)).fetchone():
                raise ValueError("This build already has an active job")
            job = uid()
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?)", (job, build_id, "queued", None, now()))
        self.schedule(job)
        return {"job_id": job}

    def datasets(self):
        with self.db() as db:
            result = []
            for row in db.execute("SELECT * FROM datasets ORDER BY created"):
                item = dict(row)
                item.update(json.loads(item.pop("document")))
                item["build_ids"] = [r[0] for r in db.execute("SELECT build_id FROM membership WHERE dataset_id=?", (item["id"],))]
                item["splits"] = {r[0]: r[1] for r in db.execute("SELECT family,split FROM splits WHERE dataset_id=?", (item["id"],))}
                item["versions"] = [{"id": r[0], "created": r[1]} for r in db.execute("SELECT id,created FROM versions WHERE dataset_id=? ORDER BY created DESC", (item["id"],))]
                result.append(item)
            return result

    def get_dataset(self, dataset_id):
        for dataset in self.datasets():
            if dataset["id"] == dataset_id:
                return dataset
        raise KeyError("Dataset not found")

    def update_dataset(self, changes, dataset_id=None):
        if set(changes) - {"name", "description", "exclude_redstone", "archived"}:
            raise ValueError("Unsupported dataset fields")
        if any(field in changes and type(changes[field]) is not bool for field in ("exclude_redstone", "archived")):
            raise ValueError("Dataset settings must be booleans")
        current = self.get_dataset(dataset_id) if dataset_id else {"name": "", "description": "", "exclude_redstone": True, "archived": False}
        current.update(changes)
        if not isinstance(current["name"], str) or not current["name"].strip() or len(current["name"]) > 100:
            raise ValueError("Dataset name must contain 1–100 characters")
        if not isinstance(current["description"], str) or len(current["description"]) > 10000:
            raise ValueError("Dataset description must be text of at most 10000 characters")
        document = {key: current[key] for key in ("description", "exclude_redstone", "archived")}
        with self.lock, self.db() as db:
            try:
                if dataset_id:
                    db.execute("UPDATE datasets SET name=?,document=? WHERE id=?", (current["name"].strip(), dump(document), dataset_id))
                else:
                    dataset_id = uid()
                    db.execute("INSERT INTO datasets VALUES (?,?,?,?)", (dataset_id, current["name"].strip(), dump(document), now()))
            except sqlite3.IntegrityError:
                raise ValueError("A dataset with that name already exists")
        return self.get_dataset(dataset_id)

    def membership(self, dataset_id, build_ids, include):
        dataset = self.get_dataset(dataset_id)
        if dataset["archived"]:
            raise ValueError("Restore this dataset before changing membership")
        for build_id in build_ids:
            self.get_build(build_id)
        with self.lock, self.db() as db:
            for build_id in build_ids:
                if include:
                    db.execute("INSERT OR IGNORE INTO membership VALUES (?,?)", (dataset_id, build_id))
                else:
                    db.execute("DELETE FROM membership WHERE dataset_id=? AND build_id=?", (dataset_id, build_id))
        return self.get_dataset(dataset_id)

    def assign_split(self, dataset_id, family, split):
        dataset = self.get_dataset(dataset_id)
        if split not in ("train", "validation", "test", "unassigned"):
            raise ValueError("Invalid dataset split")
        if family not in {self.get_build(i)["family"] for i in dataset["build_ids"]}:
            raise ValueError("Family is not in this dataset")
        with self.lock, self.db() as db:
            db.execute("INSERT INTO splits VALUES (?,?,?) ON CONFLICT(dataset_id,family) DO UPDATE SET split=excluded.split", (dataset_id, family, split))
        return self.get_dataset(dataset_id)

    def preview(self, dataset_id):
        dataset = self.get_dataset(dataset_id)
        entries = []
        for build_id in dataset["build_ids"]:
            build = self.get_build(build_id)
            reasons = list(build["eligibility_reasons"])
            if dataset["exclude_redstone"] and (build["redstone"] == "yes" or build["type"] == "redstone build"):
                reasons.append("Dataset excludes redstone builds")
            if not build["family_confirmed"]:
                reasons.append("Related-build family grouping is not confirmed")
            split = dataset["splits"].get(build["family"], "unassigned")
            if split == "unassigned":
                reasons.append("Family has no assigned split")
            entries.append({"build": build, "split": split, "blocking_reasons": reasons})
        ready = bool(entries) and not dataset["archived"] and all(not e["blocking_reasons"] for e in entries)
        return {"dataset": dataset, "entries": entries, "ready": ready,
                "warnings": ["Empty dataset"] if not entries else (["Dataset is archived"] if dataset["archived"] else [])}

    def export(self, dataset_id):
        with self.lock:
            preview = self.preview(dataset_id)
            if not preview["ready"]:
                raise ValueError("Dataset export blocked; inspect the export preview for reasons")
            version = uid()
            stage = self.directory / "snapshots" / f".staging-{version}"
            stage.mkdir()
            manifest = {"schema_version": 1, "id": version, "created": now(), "dataset": preview["dataset"], "builds": []}
            try:
                for entry in preview["entries"]:
                    build = deepcopy(entry["build"])
                    conversion = build["conversion"]
                    source = self.directory / "sources" / f"{build['source_hash']}.litematic"
                    converted = self.directory / "conversions" / f"{conversion['id']}.litematic"
                    for side, path, expected in (("source", source, build["source_hash"]), ("converted", converted, conversion["artifact_hash"])):
                        if cmp.digest(path) != expected:
                            raise ValueError("An artifact changed during export")
                        filename = f"{build['id']}-{side}.litematic"
                        shutil.copyfile(path, stage / filename)
                        if cmp.digest(stage / filename) != expected:
                            raise ValueError("Copied artifact failed hash verification")
                        build[f"{side}_artifact"] = filename
                    build["split"] = entry["split"]
                    manifest["builds"].append(build)
                with (stage / "manifest.json").open("x", encoding="utf-8") as handle:
                    json.dump(manifest, handle, indent=2)
                destination = self.directory / "snapshots" / version
                stage.rename(destination)
                with self.db() as db:
                    db.execute("INSERT INTO versions VALUES (?,?,?,?)", (version, dataset_id, dump(manifest), manifest["created"]))
            except Exception:
                # Preserve any partial artifacts for diagnostics; never publish them as a version.
                raise
            return {"id": version, "manifest": manifest}

    def version(self, dataset_id, version_id):
        with self.db() as db:
            row = db.execute("SELECT manifest FROM versions WHERE id=? AND dataset_id=?", (version_id, dataset_id)).fetchone()
            if not row:
                raise KeyError("Dataset version not found")
            return json.loads(row[0])
