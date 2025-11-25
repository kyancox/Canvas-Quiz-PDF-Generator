#!/usr/bin/env python3
"""
Canvas Quiz PDF Generator - Main CLI

Usage:
    python run_quiz.py --quiz 5 --csv "Quiz 5.csv" --limit 5
    
Generates individual PDFs per question type per student.
"""

import asyncio
import argparse
import sys
from importlib import import_module
from pathlib import Path
from core.orchestrator import process_quiz


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Generate Canvas quiz PDFs by question type',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test with 5 students
  python run_quiz.py --quiz 5 --csv "Quiz 5 - Network Flow.csv" --limit 5
  
  # Full run (all students)
  python run_quiz.py --quiz 5 --csv "Quiz 5 - Network Flow.csv"
  
  # Generate PDFs for one specific student
  python run_quiz.py --quiz 5 --csv "Quiz 5.csv" --student "Alice Smith"
  
  # Skip zip file creation (faster for testing)
  python run_quiz.py --quiz 5 --csv "Quiz 5.csv" --limit 3 --no-zip
  
  # Force regenerate templates
  python run_quiz.py --quiz 5 --csv "Quiz 5.csv" --regenerate

  # Build only blank Gradescope templates (no CSV needed)
  python run_quiz.py --quiz 5 --templates-only

  # Skip blank template generation for a faster student-only run
  python run_quiz.py --quiz 5 --csv "Quiz 5.csv" --no-templates
        """
    )
    
    parser.add_argument(
        '--quiz',
        type=int,
        required=True,
        help='Quiz number (1-6)'
    )
    
    parser.add_argument(
        '--csv',
        type=str,
        help='Path to Canvas CSV export (required unless --templates-only)'
    )
    
    parser.add_argument(
        '--limit',
        type=int,
        help='Limit number of students (for testing, recommended: 10 or less)'
    )
    
    parser.add_argument(
        '--student',
        type=str,
        help='Generate PDFs for specific student by name (case-insensitive, partial match)'
    )
    
    parser.add_argument(
        '--templates-only',
        action='store_true',
        help='Only generate blank Gradescope templates and skip student PDFs'
    )

    parser.add_argument(
        '--no-templates',
        action='store_true',
        help='Skip generating blank Gradescope templates'
    )

    parser.add_argument(
        '--no-zip',
        action='store_true',
        help='Skip creating zip file at the end'
    )
    
    parser.add_argument(
        '--regenerate',
        action='store_true',
        help='Force regenerate HTML templates from rubric'
    )

    parser.add_argument(
        '--browsers',
        type=int,
        default=1,
        help='Number of headless browsers to use in parallel for PDF rendering'
    )
    
    args = parser.parse_args()
    
    if args.templates_only and args.no_templates:
        print("❌ Cannot combine --templates-only and --no-templates")
        sys.exit(1)
    
    if not args.templates_only:
        if not args.csv:
            print("❌ --csv is required unless --templates-only is set")
            sys.exit(1)
        if not Path(args.csv).exists():
            print(f"❌ Error: CSV file not found: {args.csv}")
            sys.exit(1)
    
    # Load quiz config
    try:
        config_module = import_module(f'configs.quiz{args.quiz}_config')
        config = config_module.QUIZ_CONFIG
    except ImportError as e:
        print(f"❌ Error: configs/quiz{args.quiz}_config.py not found")
        print(f"   Create it by copying configs.example/quiz_config_template.py")
        print(f"   Error details: {e}")
        sys.exit(1)
    except AttributeError:
        print(f"❌ Error: configs/quiz{args.quiz}_config.py missing QUIZ_CONFIG variable")
        sys.exit(1)
    
    csv_path = args.csv if args.csv else None

    # Run workflow
    try:
        asyncio.run(process_quiz(
            csv_path,
            config,
            limit=args.limit,
            student_name=args.student,
            skip_zip=args.no_zip,
            force_regenerate=args.regenerate,
            templates_only=args.templates_only,
            generate_templates=not args.no_templates,
            browsers=args.browsers
        ))
    except KeyboardInterrupt:
        print("\n\n⚠ Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error during processing: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()

