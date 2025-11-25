"""
Orchestrator: Main workflow that ties all modules together.

Coordinates:
1. Template generation (or loading)
2. CSV parsing
3. HTML generation per student
4. PDF rendering
"""

import asyncio
import shutil
from pathlib import Path
from typing import Dict, Optional
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.live import Live
from .rubric_converter import convert_rubric_to_templates, save_templates
from .csv_parser import CanvasCSVParser
from .html_generator import (
    generate_student_html,
    load_template,
    sanitize_filename,
    generate_blank_template_html
)
from .pdf_generator import BrowserPool
from .zip_creator import create_quiz_zip

console = Console()


def load_or_generate_templates(config: dict, force_regenerate: bool = False) -> Dict[str, str]:
    """
    Load existing templates or generate new ones from rubric.
    
    Args:
        config: Quiz configuration
        force_regenerate: If True, regenerate even if templates exist
        
    Returns:
        Dict mapping group_id → HTML template
    """
    quiz_id = config['quiz_id']
    templates_dir = Path(f"templates/quiz{quiz_id}")
    
    templates = {}
    all_exist = True
    
    # Check if templates already exist
    for group in config['question_groups']:
        template_file = templates_dir / f"{group['id']}_template.html"
        if not template_file.exists():
            all_exist = False
            break
    
    # Generate if needed
    if not all_exist or force_regenerate:
        console.print("[cyan]📝 Generating templates from rubric...[/cyan]")
        templates = convert_rubric_to_templates(config)
        save_templates(templates, quiz_id)
    else:
        # Load existing templates
        console.print(f"[cyan]📂 Loading existing templates from {templates_dir}/[/cyan]")
        for group in config['question_groups']:
            template_file = templates_dir / f"{group['id']}_template.html"
            templates[group['id']] = load_template(str(template_file))
            console.print(f"   [green]✓[/green] Loaded {group['id']}_template.html")
    
    return templates


async def generate_blank_templates(config: dict, templates: Dict[str, str], browser_pool: BrowserPool) -> None:
    """Create Gradescope-ready blank HTML and PDF templates per question."""
    quiz_id = config['quiz_id']
    template_html_root = Path(f"output/quiz{quiz_id}/templates/html")
    template_pdf_root = Path(f"output/quiz{quiz_id}/templates/pdf")
    template_html_root.mkdir(parents=True, exist_ok=True)
    template_pdf_root.mkdir(parents=True, exist_ok=True)

    template_images_src = Path(f"templates/quiz{quiz_id}/images")
    template_images_root = template_html_root / "images"
    if template_images_src.exists():
        if template_images_root.exists():
            shutil.rmtree(template_images_root)
        shutil.copytree(template_images_src, template_images_root)
        console.print(f"   [green]✓[/green] Copied template images to {template_images_root}/")
    else:
        console.print(f"   [yellow]⚠[/yellow] Missing template images at {template_images_src}")

    for group in config['question_groups']:
        group_id = group['id']
        console.print(f"\n[cyan]📄 Generating blank template for {group_id}[/cyan]")
        blank_html = generate_blank_template_html(
            templates[group_id],
            group,
            variant_to_show=group.get('template_variant', 1)
        )

        group_html_dir = template_html_root / group_id
        group_html_dir.mkdir(parents=True, exist_ok=True)

        group_images_dir = group_html_dir / "images"
        if template_images_src.exists():
            if group_images_dir.exists():
                shutil.rmtree(group_images_dir)
            shutil.copytree(template_images_src, group_images_dir)

        html_path = group_html_dir / f"{group_id}_blank_template.html"
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(blank_html)

        pdf_path = template_pdf_root / f"{group_id}_blank_template.pdf"
        success = await browser_pool.generate_pdf(blank_html, str(pdf_path), str(html_path))

        if success:
            console.print(f"   [green]✓[/green] Blank PDF ready: {pdf_path}")
        else:
            console.print(f"   [red]✗[/red] Failed to render blank PDF for {group_id}")


async def process_quiz(
    csv_path: Optional[str],
    config: dict,
    limit: Optional[int] = None,
    student_name: Optional[str] = None,
    skip_zip: bool = False,
    force_regenerate: bool = False,
    templates_only: bool = False,
    generate_templates: bool = True,
    browsers: int = 1
) -> None:
    """
    Main workflow: CSV → HTML → PDFs
    
    Args:
        csv_path: Path to Canvas CSV export (optional when templates_only)
        config: Quiz configuration dict
        limit: Optional limit on number of students (for testing)
        student_name: Optional student name filter (case-insensitive, partial match)
        skip_zip: If True, skip creating zip file at the end
        force_regenerate: Force regeneration of templates
        templates_only: If True, only produce blank templates
        generate_templates: If False, skip blank template generation
        browsers: Number of Playwright browser instances to use
    """
    quiz_id = config['quiz_id']
    quiz_name = config['quiz_name']
    
    # Display header
    header_text = f"[bold cyan]Canvas Quiz PDF Generator[/bold cyan]\n[yellow]Quiz {quiz_id}: {quiz_name}[/yellow]"
    console.print(Panel(header_text, border_style="cyan", padding=(1, 2)))
    
    # Step 1: Load or generate templates
    templates = load_or_generate_templates(config, force_regenerate)

    async with BrowserPool(max_browsers=browsers) as browser_pool:
        if generate_templates:
            await generate_blank_templates(config, templates, browser_pool)
        else:
            console.print(f"[dim]⏭ Skipping blank template generation (--no-templates)[/dim]")

        if templates_only:
            console.print(f"\n[dim]⏭ Templates-only mode: skipping student PDFs[/dim]")
            return

        if csv_path is None:
            console.print("[red]✗[/red] CSV path is required to generate student files")
            return

        # Step 2: Parse CSV
        console.print(f"\n[cyan]📊 Parsing CSV...[/cyan]")
        parser = CanvasCSVParser(csv_path, config)
        all_students = parser.get_student_data(limit=None)  # Get all students first

        # Filter by student name if provided
        if student_name:
            if limit:
                console.print(f"   [yellow]⚠[/yellow] Note: --limit ignored when using --student filter")
            search_name = student_name.lower().strip()
            students = [
                s for s in all_students
                if search_name in s['name'].lower()
            ]
            if not students:
                console.print(f"\n[red]✗[/red] Error: No students found matching '{student_name}'")
                console.print(f"   [dim]Available students: {', '.join([s['name'] for s in all_students[:10]])}...[/dim]")
                return
            console.print(f"   [green]✓[/green] Found {len(students)} student(s) matching '{student_name}'")
            if len(students) > 1:
                console.print(f"   [yellow]⚠[/yellow] Multiple matches: {', '.join([s['name'] for s in students])}")
        else:
            students = all_students
            if limit:
                students = students[:limit]
                console.print(f"   [yellow]⚠ Limited to {limit} students for testing[/yellow]")

        # Copy images to output folder for each question group
        console.print(f"\n[cyan]📁 Setting up output directories...[/cyan]")
        for group in config['question_groups']:
            group_id = group['id']
            group_name = group['name']
            base_dir = f"output/quiz{quiz_id}/{group_id}_{group_name.lower().replace(' ', '_')}"

            # Copy images from templates to output
            template_images = Path(f"templates/quiz{quiz_id}/images")
            output_images = Path(f"{base_dir}/html/images")

            if template_images.exists():
                if output_images.exists():
                    shutil.rmtree(output_images)
                shutil.copytree(template_images, output_images)
                console.print(f"   [green]✓[/green] Copied images to {base_dir}/html/images/")

        # Step 3: Generate PDFs for each student with progress bar
        total_pdfs = len(students) * len(config['question_groups'])
        console.print(
            f"\n[cyan]📄 Generating {total_pdfs} PDFs[/cyan] [dim]({len(students)} students × {len(config['question_groups'])} questions)[/dim]"
        )
        console.print(f"[dim]Using {browsers} browser(s) in the pool[/dim]")

        import time
        overall_start = time.time()

        jobs = []
        for student in students:
            for group in config['question_groups']:
                group_id = group['id']
                group_name = group['name']

                page_break_mode = group.get('page_break', 'same-page')
                num_parts = group.get('num_parts')
                html = generate_student_html(
                    templates[group_id],
                    student,
                    group_id,
                    page_break_mode=page_break_mode,
                    num_parts=num_parts
                )

                safe_name = sanitize_filename(student['name'])
                variant = student[group_id]['variant']

                base_dir = f"output/quiz{quiz_id}/{group_id}_{group_name.lower().replace(' ', '_')}"
                html_dir = f"{base_dir}/html"
                pdf_dir = f"{base_dir}/pdf"

                variant_filename = f"{group_id}v{variant}_nf_{safe_name}"
                html_path = f"{html_dir}/{variant_filename}.html"
                pdf_path = f"{pdf_dir}/{variant_filename}.pdf"

                Path(html_dir).mkdir(parents=True, exist_ok=True)
                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(html)

                jobs.append((html, pdf_path, html_path))

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("•"),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
        )

        pdf_count = 0

        async def render_job(html_content: str, pdf_path: str, html_path: str):
            result = await browser_pool.generate_pdf(html_content, pdf_path, html_path)
            progress.update(task_id, advance=1)
            return result

        with Live(progress, console=console, refresh_per_second=4):
            task_id = progress.add_task(
                "[cyan]Rendering PDFs...",
                total=len(jobs)
            )

            tasks = [asyncio.create_task(render_job(*job)) for job in jobs]
            for task in asyncio.as_completed(tasks):
                if await task:
                    pdf_count += 1

        total_time = time.time() - overall_start

        summary_text = f"""[bold cyan]Summary:[/bold cyan]
  PDFs generated: [green]{pdf_count}[/green]/[cyan]{total_pdfs}[/cyan]
  Output directory: [yellow]output/quiz{quiz_id}/[/yellow]

  [bold cyan]Timing:[/bold cyan]
  Total: [green]{total_time:.1f}s[/green] ({total_time/60:.1f}m)

  [dim]├── {config['question_groups'][0]['id']}_{config['question_groups'][0]['name'].lower().replace(' ', '_')}/[/dim]
  [dim]│   ├── html/ ({len(students)} files)[/dim]
  [dim]│   └── pdf/ ({len(students)} files)[/dim]
  [dim]└── {config['question_groups'][1]['id']}_{config['question_groups'][1]['name'].lower().replace(' ', '_')}/[/dim]
  [dim]    ├── html/ ({len(students)} files)[/dim]
  [dim]    └── pdf/ ({len(students)} files)[/dim]"""

        console.print(Panel(summary_text, border_style="green", padding=(1, 2)))
    
    # Create zip file automatically (unless skipped)
    if not skip_zip:
        console.print(f"\n[cyan]📦 Creating zip file...[/cyan]")
        zip_success = create_quiz_zip(quiz_id)
        if zip_success:
            zip_path = Path(f"output/quiz{quiz_id}pdfs.zip")
            if zip_path.exists():
                zip_size = zip_path.stat().st_size / (1024 * 1024)  # MB
                console.print(f"   [green]✓[/green] Created: {zip_path}")
                console.print(f"   [dim]Size: {zip_size:.1f} MB[/dim]")
        else:
            console.print(f"   [yellow]⚠[/yellow] Warning: Could not create zip file")
    else:
        console.print(f"\n[dim]⏭ Skipping zip file creation (--no-zip)[/dim]")

