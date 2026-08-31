import argparse
import errno
import subprocess
import sys
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from review_pipeline.config import OUTPUT_DIR, PROJECT_ROOT


COLLECT_STAGES = (
    "review_pipeline.stages.discover",
    "review_pipeline.stages.scrape",
    "review_pipeline.stages.extract",
    "review_pipeline.stages.normalize",
)

GENERATE_STAGES = (
    "review_pipeline.stages.setup_watermarks",
    "review_pipeline.stages.plan",
    "review_pipeline.stages.generate",
    "review_pipeline.stages.validate",
    "review_pipeline.stages.audit_initial",
    "review_pipeline.stages.repair",
    "review_pipeline.stages.audit_final",
    "review_pipeline.stages.summarize",
    "review_pipeline.stages.clean",
    "review_pipeline.stages.render",
)

REFRESHABLE_STAGES = {
    "review_pipeline.stages.discover",
    "review_pipeline.stages.scrape",
    "review_pipeline.stages.extract",
    "review_pipeline.stages.generate",
    "review_pipeline.stages.plan",
    "review_pipeline.stages.audit_initial",
    "review_pipeline.stages.repair",
    "review_pipeline.stages.audit_final",
}


def add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore relevant caches and repeat paid API work",
    )


def add_preview_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Preview server port (default: 8000; busy ports use the next available port)",
    )
    parser.add_argument(
        "--no-serve",
        action="store_true",
        help="Write files only; do not start a server or open Chrome",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Arzopa Z3FC review pipeline. 'generate' and 'all' serve review.html "
            "and open its preview in Google Chrome by default."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)

    collect = commands.add_parser("collect", help="Collect and normalize review evidence")
    add_common_options(collect)

    generate = commands.add_parser(
        "generate",
        help="Generate files, start the preview server, and open Chrome",
        description=(
            "Generate the review, then start a local preview server and open "
            "review.html in Google Chrome. Use --no-serve for files only."
        ),
    )
    add_common_options(generate)
    add_preview_options(generate)

    all_stages = commands.add_parser(
        "all",
        help="Collect, generate, serve the review, and open Chrome",
        description=(
            "Collect evidence and generate the review, then start a local preview "
            "server and open review.html in Google Chrome. Use --no-serve for files only."
        ),
    )
    add_common_options(all_stages)
    add_preview_options(all_stages)
    return parser.parse_args()


def run_stage(module: str, refresh: bool) -> None:
    command = [sys.executable, "-m", module]
    if refresh and module in REFRESHABLE_STAGES:
        command.append("--refresh")
    print(f"\n=== {module.rsplit('.', 1)[-1]} ===", flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def run_stages(stages: tuple[str, ...], refresh: bool) -> None:
    for module in stages:
        run_stage(module, refresh)


def open_chrome(url: str) -> None:
    if sys.platform == "darwin":
        result = subprocess.run(
            ["open", "-a", "Google Chrome", url],
            check=False,
            capture_output=True,
        )
        if result.returncode == 0:
            return
    webbrowser.open(url)


def create_server(port: int) -> ThreadingHTTPServer:
    handler = partial(SimpleHTTPRequestHandler, directory=str(OUTPUT_DIR))
    try:
        return ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as error:
        if error.errno != errno.EADDRINUSE:
            raise
        print(f"Port {port} is busy; selecting an available port.")
        return ThreadingHTTPServer(("127.0.0.1", 0), handler)


def serve_review(port: int) -> None:
    with create_server(port) as server:
        actual_port = server.server_address[1]
        url = f"http://127.0.0.1:{actual_port}/review.html"
        print(f"\nStarting preview server and opening Google Chrome: {url}")
        print("Press Ctrl+C to stop the server.")
        open_chrome(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nPreview server stopped.")


def main() -> None:
    args = parse_args()
    if args.command in {"collect", "all"}:
        run_stages(COLLECT_STAGES, args.refresh)
    if args.command in {"generate", "all"}:
        run_stages(GENERATE_STAGES, args.refresh)
        if not args.no_serve:
            serve_review(args.port)


if __name__ == "__main__":
    main()
