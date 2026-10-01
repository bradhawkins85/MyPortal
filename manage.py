#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

from app.core.database import db


async def rebuild(args: argparse.Namespace) -> None:
    from app.services import rag_relationships as rel_service
    from app.services import rag_index as rag_index_service

    await db.connect()
    try:
        await db.run_migrations()
        if args.document:
            doc_type, _, doc_id = args.document.partition(":")
            doc = await rag_index_service.rag_repo.get_document_by_source(
                doc_type, doc_id, rag_index_service.embedding_model()
            )
            if not doc:
                raise SystemExit(f"Document not found: {args.document}")
            queued = await rel_service.enqueue_relationships_for_document(int(doc["id"]))
        else:
            source_filters = []
            if args.tickets:
                source_filters.append("tickets")
            if args.knowledge_base:
                source_filters.append("knowledge_base")
            if args.products:
                source_filters.append("products")
            if args.assets:
                source_filters.append("assets")
            if args.issues:
                source_filters.append("issues")
            where = ["is_active = 1"]
            params = []
            if source_filters and not args.all:
                where.append("source_type IN (" + ",".join("?" for _ in source_filters) + ")")
                params.extend(source_filters)
            if args.company:
                where.append("company_id = ?")
                params.append(int(args.company))
            rows = await db.fetch_all(
                f"SELECT id FROM rag_documents WHERE {' AND '.join(where)} ORDER BY id",
                tuple(params),
            )
            queued = 0
            for row in rows:
                # Current relationships and unchanged pairs are skipped by the repository.
                queued += await rel_service.enqueue_relationships_for_document(int(row["id"]))
        print(f"Queued {queued} RAG relationship job(s).")
    finally:
        await db.disconnect()


async def migrate(args: argparse.Namespace) -> None:
    """Run the deployment's single, locked schema phase."""
    await db.connect()
    try:
        await db.run_migrations(
            serving_release=args.serving_release,
            target_release=args.target_release,
            maintenance=args.maintenance,
        )
    finally:
        await db.disconnect()
    # Filesystem-only and idempotent; never fail the schema phase over it.
    try:
        from app.services import ticket_attachments as attachments_service

        attachments_service.migrate_legacy_attachment_files()
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Legacy ticket attachment migration skipped: {exc}")


def migrate_legacy_ticket_attachments(args: argparse.Namespace) -> None:
    """Move legacy static/uploads/tickets files into private storage (idempotent)."""
    from app.services import ticket_attachments as attachments_service

    counts = attachments_service.migrate_legacy_attachment_files(dry_run=args.dry_run)
    prefix = "[dry run] " if args.dry_run else ""
    print(
        f"{prefix}moved={counts['moved']} duplicates_removed={counts['duplicates_removed']} "
        f"conflicts={counts['conflicts']} skipped={counts['skipped']}"
    )
    if counts["conflicts"]:
        raise SystemExit(1)


async def m365_spam_worker(args: argparse.Namespace) -> None:
    """Long-running worker that processes queued M365 spam search/purge requests."""
    from app.services import m365_spam_purge as purge_service

    await db.connect()
    poll_interval = args.interval
    print(f"M365 spam purge worker started (poll every {poll_interval}s). Ctrl+C to stop.")
    try:
        while True:
            try:
                processed = await purge_service.process_queued()
                if processed:
                    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] Processed {processed} request(s).")
            except Exception as exc:
                print(f"  Error: {exc}", file=sys.stderr)
            await asyncio.sleep(poll_interval)
    except asyncio.CancelledError:
        pass
    finally:
        await db.disconnect()
        print("Worker stopped.")


async def m365_spam_process_once(args: argparse.Namespace) -> None:
    """Process all currently queued M365 spam requests and exit (for cron)."""
    from app.services import m365_spam_purge as purge_service

    await db.connect()
    try:
        processed = await purge_service.process_queued()
        print(f"Processed {processed} request(s).")
    finally:
        await db.disconnect()


async def export_related_feedback(args: argparse.Namespace) -> None:
    import json
    from pathlib import Path

    from app.repositories import rag_relationships as rel_repo

    await db.connect()
    try:
        await db.run_migrations()
        dataset = rel_repo.build_related_feedback_dataset(
            await rel_repo.list_relationship_feedback_labels()
        )
    finally:
        await db.disconnect()
    Path(args.output).write_text(json.dumps(dataset, indent=2) + "\n")
    print(f"labels={len(dataset['labels'])} output={args.output}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="manage.py")
    sub = parser.add_subparsers(dest="command", required=True)
    migrate_parser = sub.add_parser("migrate", help="apply and validate pending migrations")
    migrate_parser.add_argument("--serving-release")
    migrate_parser.add_argument("--target-release", required=True)
    migrate_parser.add_argument("--maintenance", action="store_true")
    rebuild_parser = sub.add_parser("rebuild-rag-relationships")
    rebuild_parser.add_argument("--all", action="store_true")
    rebuild_parser.add_argument("--tickets", action="store_true")
    rebuild_parser.add_argument("--knowledge-base", action="store_true")
    rebuild_parser.add_argument("--products", action="store_true")
    rebuild_parser.add_argument("--assets", action="store_true")
    rebuild_parser.add_argument("--issues", action="store_true")
    rebuild_parser.add_argument("--changed-only", action="store_true")
    rebuild_parser.add_argument("--company")
    rebuild_parser.add_argument("--document")
    legacy_parser = sub.add_parser(
        "migrate-legacy-ticket-attachments",
        help="move static/uploads/tickets files into private_uploads/tickets",
    )
    legacy_parser.add_argument("--dry-run", action="store_true")
    # M365 spam purge commands
    m365_worker_parser = sub.add_parser(
        "m365-spam-worker",
        help="long-running worker that processes queued M365 spam search/purge requests",
    )
    m365_worker_parser.add_argument(
        "--interval", type=int, default=10, help="poll interval in seconds (default: 10)"
    )
    sub.add_parser(
        "m365-spam-process-once",
        help="process all currently queued M365 spam requests and exit (for cron)",
    )
    export_parser = sub.add_parser(
        "export-related-feedback",
        help="write technician Related-item votes as an evals/ai_quality dataset",
    )
    export_parser.add_argument("--output", default="evals/ai_quality/related_feedback.json")
    args = parser.parse_args()
    if args.command == "migrate":
        asyncio.run(migrate(args))
    elif args.command == "rebuild-rag-relationships":
        asyncio.run(rebuild(args))
    elif args.command == "migrate-legacy-ticket-attachments":
        migrate_legacy_ticket_attachments(args)
    elif args.command == "m365-spam-worker":
        asyncio.run(m365_spam_worker(args))
    elif args.command == "m365-spam-process-once":
        asyncio.run(m365_spam_process_once(args))
    elif args.command == "export-related-feedback":
        asyncio.run(export_related_feedback(args))


if __name__ == "__main__":
    main()
