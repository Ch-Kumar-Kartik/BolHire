"""The 24 SAMPLE job listings used by the demo.

Every company below is INVENTED. These are sample listings for a demonstration, not live vacancies, and none of them refers to a real
employer, product or opening. `required_skills` ORDER is part of the data: the seed script treats a reordered list as a change.
Content rules (counts, distributions, forbidden content) are enforced by catalogue_validation.py before any database access.
"""

import re
import unicodedata
from dataclasses import dataclass

SAMPLE_COUNT = 24
REMOTE_LABEL = "Remote (India)"
# The only accepted location label for each city key (checked by the validator).
LOCATION_LABELS: dict[str, str] = {
    "delhi": "New Delhi, Delhi", "noida": "Noida, Uttar Pradesh", "gurugram": "Gurugram, Haryana",
    "bengaluru": "Bengaluru, Karnataka", "hyderabad": "Hyderabad, Telangana", "pune": "Pune, Maharashtra",
    "mumbai": "Mumbai, Maharashtra", "chennai": "Chennai, Tamil Nadu", "kolkata": "Kolkata, West Bengal",
    "jaipur": "Jaipur, Rajasthan",
}


@dataclass(frozen=True)
class CatalogueJob:
    slug: str
    title: str
    company: str
    location_label: str
    description: str
    employment_type: str
    role_key: str
    city_key: str | None
    work_mode: str
    min_experience_years: float | None
    max_experience_years: float | None
    required_skills: tuple[str, ...]
    is_sample: bool = True
    is_active: bool = True


def slugify(title: str, company: str) -> str:
    """Lowercase kebab of "<title>-<company>"."""
    text = unicodedata.normalize("NFKC", f"{title} {company}").casefold()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _job(title, company, role_key, city_key, work_mode, employment_type, min_years, max_years, skills, description) -> CatalogueJob:
    return CatalogueJob(
        slug=slugify(title, company), title=title, company=company,
        location_label=REMOTE_LABEL if work_mode == "remote" else LOCATION_LABELS[city_key],
        description=description, employment_type=employment_type, role_key=role_key, city_key=city_key, work_mode=work_mode,
        min_experience_years=min_years, max_experience_years=max_years, required_skills=tuple(skills),
    )


CATALOGUE: tuple[CatalogueJob, ...] = (
    _job("Backend Developer", "Kestrelbyte Technologies", "backend-developer", "pune", "hybrid", "full_time", 2, 5,
         ("python", "django", "postgresql", "rest apis", "docker", "git"),
         "Build and maintain the Django services behind an order-tracking platform. You will design REST endpoints, write PostgreSQL "
         "migrations, review pull requests and improve query performance on busy tables. The team ships weekly from Docker-based "
         "environments, so you will also help keep the build pipeline reliable and document the services you own for the wider "
         "engineering group."),
    _job("Senior Backend Engineer", "Orbitwell Systems", "backend-developer", "bengaluru", "onsite", "full_time", 5, None,
         ("java", "spring boot", "mysql", "redis", "kubernetes", "aws", "ci/cd"),
         "Lead the design of Java and Spring Boot microservices that process high volumes of logistics events. You will set service "
         "boundaries, tune MySQL and Redis usage, mentor engineers through design reviews and own the rollout of services on "
         "Kubernetes in the cloud. Expect to run incident reviews, define service-level objectives and keep the platform dependable "
         "as traffic grows."),
    _job("Junior Backend Developer", "Lumenary Digital", "backend-developer", "noida", "onsite", "full_time", 0, 2,
         ("python", "fastapi", "sql", "git", "rest apis"),
         "Join a small product team building internal tools in Python with FastAPI. You will implement well-scoped endpoints, write "
         "SQL queries for reports, add tests and fix defects found by the support team. A senior engineer pairs with you on design "
         "decisions, and you will learn how code moves from a pull request to production in this office-based team."),
    _job("Backend Developer (Node.js)", "Vantaro Labs", "backend-developer", None, "remote", "full_time", 1, 3,
         ("node.js", "typescript", "mongodb", "rest apis", "docker", "git"),
         "Develop Node.js and TypeScript APIs for a subscription billing product. You will model data in MongoDB, expose REST "
         "endpoints, write integration tests and package services in Docker. The team works remotely across India with overlapping "
         "hours, so clear written updates and small, frequent pull requests are part of the daily routine."),
    _job("Frontend Developer", "Brightloom Software", "frontend-developer", "hyderabad", "hybrid", "full_time", 1, 3,
         ("react", "javascript", "html", "css", "git", "jest"),
         "Create responsive screens in React for a learning platform used by school students. You will turn design files into "
         "accessible components, write unit tests with Jest, fix browser issues and work with backend colleagues on API contracts. "
         "The team meets in the Hyderabad office three days a week and works from home on the remaining days."),
    _job("Senior Frontend Engineer", "Zephyrnest Cloud", "frontend-developer", None, "remote", "full_time", 5, None,
         ("typescript", "react", "javascript", "css", "jest", "ci/cd"),
         "Own the TypeScript and React codebase for a cloud cost dashboard. You will define component standards, improve rendering "
         "performance on large tables, guide code reviews and keep the continuous delivery pipeline healthy. This role is fully "
         "remote within India and involves regular design discussions with product and platform teams."),
    _job("Frontend Developer (Angular)", "Pixelmandi Studio", "frontend-developer", "mumbai", "onsite", "full_time", 2, 4,
         ("angular", "typescript", "html", "css", "rest apis", "git"),
         "Build Angular applications for the client portals of a design agency. You will implement layouts in HTML and CSS, structure "
         "TypeScript modules, connect screens to REST services and fix cross-browser issues. Work happens at the Mumbai studio, where "
         "developers and designers sit together and review work in progress every day."),
    _job("Full Stack Developer", "Tarkashi Analytics", "full-stack-developer", "gurugram", "hybrid", "full_time", 3, 6,
         ("react", "node.js", "javascript", "postgresql", "docker", "git"),
         "Deliver features end to end for an analytics workbench: React interfaces, Node.js services and PostgreSQL tables. You will "
         "break down requirements with product managers, build and test each layer, containerise releases with Docker and support the "
         "features after launch. The team works hybrid from the Gurugram office."),
    _job("Full Stack Engineer", "Quillgrid Data", "full-stack-developer", "bengaluru", "hybrid", "full_time", 5, None,
         ("python", "django", "react", "postgresql", "aws", "docker", "git"),
         "Take ownership of a data-quality product built with Django, React and PostgreSQL on AWS. You will shape the architecture, "
         "review designs from other engineers, plan releases and help new colleagues become productive. Experience running services in "
         "production and explaining trade-offs to non-engineers is important in this hybrid Bengaluru role."),
    _job("Full Stack Intern", "Mintwave Mobility", "full-stack-developer", "jaipur", "onsite", "internship", 0, 1,
         ("javascript", "html", "css", "node.js", "git"),
         "A hands-on internship for students finishing a degree and recent graduates. You will build small features using HTML, CSS, "
         "JavaScript and Node.js, track your work in Git and present what you built to the team every fortnight. A mentor reviews your "
         "code each week at the Jaipur office, and no earlier professional experience is expected."),
    _job("Android Developer", "Mintwave Mobility", "mobile-developer", "chennai", "hybrid", "full_time", 2, 5,
         ("kotlin", "android", "rest apis", "git", "sql"),
         "Develop Android features in Kotlin for a ride-sharing app aimed at small cities. You will build screens, consume REST APIs, "
         "store data locally with SQL and write tests for the main user journeys. The Chennai team works hybrid, releases every two "
         "weeks and reviews crash reports together."),
    _job("Cross-Platform Mobile Developer", "Sunrill Commerce", "mobile-developer", None, "remote", "full_time", 1, 3,
         ("flutter", "react native", "android", "ios", "rest apis", "git"),
         "Build one shopping app for Android and iOS using Flutter and React Native components. You will implement product pages and "
         "checkout flows, integrate REST APIs, profile performance on low-end phones and publish builds through the store pipelines. "
         "The role is remote within India with a weekly team call."),
    _job("Data Analyst", "Tarkashi Analytics", "data-analyst", "delhi", "onsite", "full_time", 1, 3,
         ("sql", "excel", "power bi", "python", "pandas"),
         "Turn raw sales and customer records into reports that leaders can act on. You will write SQL queries, clean data with Python "
         "and pandas, build Power BI dashboards and check numbers with business teams before they are shared. The analytics team sits "
         "in the New Delhi office and meets every morning."),
    _job("Data Analyst Intern", "Quillgrid Data", "data-analyst", None, "remote", "internship", 0, 1,
         ("excel", "sql", "tableau", "python"),
         "A remote internship for learners who enjoy working with numbers. You will clean spreadsheets in Excel, write simple SQL "
         "queries, build Tableau charts and explain findings in short written summaries. Experienced analysts review your work every "
         "week, and no previous job is required."),
    _job("Senior Data Analyst", "Cobaltpeak Infra", "data-analyst", "hyderabad", "hybrid", "full_time", 4, 7,
         ("sql", "python", "pandas", "tableau", "power bi", "excel"),
         "Lead analysis for infrastructure usage and capacity planning. You will define metrics, build SQL and pandas pipelines, design "
         "Tableau and Power BI dashboards, and present findings to engineering and finance leaders. You will also guide two junior "
         "analysts and help standardise how reports are checked. The team works hybrid from Hyderabad."),
    _job("Data Engineer", "Kestrelbyte Technologies", "data-engineer", "pune", "onsite", "full_time", 3, 6,
         ("python", "sql", "spark", "airflow", "aws", "postgresql", "docker"),
         "Build batch pipelines that move retail data into a warehouse. You will write Python and SQL transformations, schedule them "
         "with Airflow, process large files with Spark on AWS and monitor failures. The role is office-based in Pune and works closely "
         "with the analysts who depend on the tables you maintain."),
    _job("Senior Data Engineer", "Orbitwell Systems", "data-engineer", "bengaluru", "hybrid", "full_time", 5, None,
         ("python", "spark", "airflow", "sql", "aws", "terraform", "kubernetes"),
         "Design the data platform for shipment analytics. You will architect Spark and Airflow workflows, manage cloud resources with "
         "Terraform on Kubernetes, set data-quality checks and mentor engineers on pipeline design. The role is hybrid in Bengaluru and "
         "carries clear ownership of reliability and cost."),
    _job("DevOps Engineer", "Zephyrnest Cloud", "devops-engineer", None, "remote", "full_time", 3, 6,
         ("docker", "kubernetes", "terraform", "aws", "ci/cd", "linux", "git"),
         "Keep a cloud platform fast and reliable. You will automate infrastructure with Terraform, run containers on Kubernetes, build "
         "CI/CD pipelines, patch Linux servers and respond to alerts on a shared rota. This is a remote role within India, with "
         "documentation and handovers as part of every change."),
    _job("DevOps Engineer (Azure)", "Cobaltpeak Infra", "devops-engineer", "mumbai", "onsite", "contract", 2, 5,
         ("azure", "linux", "docker", "ci/cd", "terraform", "git"),
         "A twelve-month contract to move internal systems to Azure. You will build CI/CD pipelines, containerise services with Docker, "
         "manage Terraform modules, harden Linux hosts and write runbooks for the support team. The work is based at the Mumbai office."),
    _job("QA Engineer", "Ravenfield Testworks", "qa-engineer", "pune", "hybrid", "full_time", 2, 4,
         ("selenium", "pytest", "python", "sql", "git", "ci/cd"),
         "Test web and API features for a retail platform before every release. You will write Selenium and pytest automation in "
         "Python, check data with SQL, log clear defect reports and run suites in the CI/CD pipeline. The Pune team works hybrid and "
         "holds a short quality review after each sprint."),
    _job("QA Automation Engineer", "Brightloom Software", "qa-engineer", "hyderabad", "onsite", "full_time", 3, 6,
         ("selenium", "javascript", "jest", "rest apis", "git", "ci/cd"),
         "Own automated testing for a student-facing web product. You will build Selenium and Jest suites, test REST endpoints, keep "
         "tests stable in CI/CD and track coverage across releases. Working from the Hyderabad office, you will sit with developers to "
         "catch issues early."),
    _job("Manual QA Tester", "Ravenfield Testworks", "qa-engineer", "kolkata", "onsite", "part_time", None, 2,
         ("manual testing", "sql", "rest apis"),
         "A part-time role for careful testers. You will follow written test cases, explore features for problems, check records with "
         "simple SQL queries, test REST endpoints through shared tools and record issues clearly. Hours are agreed with the Kolkata "
         "office team, and the work suits someone early in their testing career."),
    _job("UI/UX Designer", "Pixelmandi Studio", "ui-ux-designer", "mumbai", "hybrid", "full_time", 2, 5,
         ("figma", "html", "css", "javascript"),
         "Design clear, accessible screens for client web products. You will run short user interviews, sketch flows, produce Figma "
         "prototypes, build small interactive demos with HTML, CSS and JavaScript, review developer builds and refine designs after "
         "testing. The hybrid Mumbai team presents work to clients every sprint."),
    _job("UI/UX Design Intern", "Sunrill Commerce", "ui-ux-designer", None, "remote", "internship", 0, 1,
         ("figma", "html", "css"),
         "A remote internship for students learning product design. You will redraw screens in Figma, test layouts with small groups "
         "of shoppers, document design decisions and learn how designers and developers work together. Mentors review your files each "
         "week, and a portfolio of class projects is enough to begin."),
)
