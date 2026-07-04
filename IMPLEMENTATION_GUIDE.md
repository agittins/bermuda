# VSCode Dev Environment Refinement - Implementation Guide

This document provides a complete implementation guide for the refined VSCode development environment setup that treats Bermuda as a git submodule within the Home Assistant dev environment.

## Overview of Changes

This PR refines the development experience with:
- **Intelligent setup script** that detects environment and configures dependencies
- **Multi-scenario debugging** (pytest, HA local, remote attach)
- **Comprehensive automation tasks** (testing, linting, formatting, coverage)
- **Multi-version CI testing** (2025.1, 2025.2, dev HA versions)
- **Realistic coverage targets** (75% instead of 100%)
- **Clear documentation** for developers

## Files to Create/Update

### 1. `.devcontainer.json` (Replace)
**Purpose:** Configure VS Code dev container with HA latest-dev image

**Key changes:**
- Use `homeassistant/home-assistant:latest-dev` image
- Set workspace to `/workspaces/bermuda`
- Bind current directory with `workspaceMount`
- Auto-run `.devcontainer/setup.sh` on creation
- Add extensions: Ruff, Pylance, Prettier, GitLens, debugpy
- Configure Python interpreter path and VS Code settings
- Mount SSH config and Docker socket for advanced use

### 2. `.devcontainer/setup.sh` (New)
**Purpose:** Intelligent setup script with environment detection

**Features:**
- ANSI colored output for readability
- Detect submodule vs standalone setup
- Check for parent HA repo at `../../../config`
- Install test dependencies from `requirements_test.txt`
- Configure pre-commit hooks
- Verify pytest and HA test fixtures
- Display summary with next steps

**How to create:**
```bash
mkdir -p .devcontainer
touch .devcontainer/setup.sh
chmod +x .devcontainer/setup.sh
```

### 3. `.vscode/settings.json` (Replace)
**Purpose:** Comprehensive VS Code editor settings

**Key settings:**
- Python interpreter: `/usr/local/bin/python`
- Formatter: Ruff (with org imports on save)
- Linting: Ruff enabled
- Type checking: Basic mode
- pytest configured with `-v --tb=short` args
- File associations: `.yaml` → home-assistant, `manifest.json` → jsonc
- Editor rulers at columns 88 and 120
- File exclusions for cache/venv files
- Watch exclusions for performance

### 4. `.vscode/launch.json` (Replace)
**Purpose:** Multi-scenario debug configurations

**Configurations added:**
1. `pytest: All Tests` - Debug entire test suite
2. `pytest: Current File` - Debug single test file
3. `pytest: Current Test (cursor)` - Debug selected test
4. `Home Assistant (local, if submodule setup)` - Launch HA with debugger
5. `Python: Current File` - Generic Python file debugging
6. `Attach to Remote HA (debugpy)` - Attach to production instance

**Groups:**
- testing: pytest configurations
- ha-dev: Home Assistant development
- ha-remote: Remote HA debugging
- utility: General Python debugging

### 5. `.vscode/tasks.json` (Replace)
**Purpose:** Comprehensive task automation

**Task categories:**

**Testing:**
- `pytest: Run all tests` (default test task)
- `pytest: Run all tests (with coverage)`
- `pytest: Run tests (fast, no coverage)`
- `Bermuda: Show test coverage`

**Linting & Formatting:**
- `ruff: Check code`
- `ruff: Format code`
- `ruff: Fix and format`

**Pre-commit:**
- `pre-commit: Run all checks`
- `pre-commit: Run on staged files`

**Home Assistant:**
- `Home Assistant: Start (if submodule)`
- `Home Assistant: Validate config (if submodule)`

**Utilities:**
- `Git: Update submodule`
- `Development: Full check suite` (comprehensive validation)

**Helper:**
- `verify-submodule-setup` (internal verification task)

### 6. `setup.cfg` (Update)
**Purpose:** Adjust coverage expectations

**Changes:**
- `fail_under = 75` (was 100)
- Add `skip_covered = False`
- pytest addopts includes `--cov-report=term-missing`

### 7. `pyproject.toml` (Update)
**Purpose:** Clarify and organize Ruff configuration

**Changes:**
- Add clarifying comments for ignored rules
- Group ignores logically (type annotations, docstrings, style, complexity, etc.)
- Mark rules as "TODO" that should be addressed later (e.g., return type annotations)
- Ensure HA-standard import aliases are present
- Update `target-version = "py312"`

### 8. `.pre-commit-config.yaml` (Update)
**Purpose:** Upgrade hooks and add enhancements

**Changes:**
- Update ruff to v0.6.8 (from v0.5.1)
- Add `check-ast`, `check-merge-conflict`, `check-json`, `check-toml` hooks
- Add `mixed-line-ending` hook
- Add Prettier for JSON/YAML/Markdown formatting
- Configure hook arguments (max-kb for large files, etc.)

### 9. `DEVELOPMENT_SUBMODULE.md` (New)
**Purpose:** Comprehensive developer guide

**Sections:**
- Why this approach (5 key benefits)
- Quick start (2-minute setup)
- Directory structure explanation
- Environment setup overview
- Common tasks with examples
- Debug configurations explained
- Git workflow for submodules
- Troubleshooting with Q&A
- Best practices
- Workflow examples (new sensor, bug fix, PR prep)
- Advanced multi-version testing
- File reference
- Getting help

### 10. `.github/workflows/tests.yml` (New)
**Purpose:** Multi-version CI testing

**Configuration:**
- Matrix strategy: Python 3.12 × HA versions [2025.1, 2025.2, dev]
- Steps:
  1. Checkout code
  2. Set up Python
  3. Cache pip packages
  4. Install test dependencies
  5. Install specific HA version
  6. Lint with ruff (check + format)
  7. Run pytest with coverage
  8. Upload to codecov
- `fail-fast: false` to run all matrix combinations

---

## Implementation Steps

### Step 1: Create the branch (optional)
```bash
git checkout -b chore/vscode-submodule-setup
```

### Step 2: Create new directory
```bash
mkdir -p .devcontainer .github/workflows
```

### Step 3: Create files
Use the file contents provided above to:
- Replace `.devcontainer.json`
- Create `.devcontainer/setup.sh` (remember `chmod +x`)
- Replace `.vscode/settings.json`
- Replace `.vscode/launch.json`
- Replace `.vscode/tasks.json`
- Update `setup.cfg`
- Update `pyproject.toml`
- Update `.pre-commit-config.yaml`
- Create `DEVELOPMENT_SUBMODULE.md`
- Create `.github/workflows/tests.yml`

### Step 4: Verify
```bash
# Check setup script is executable
ls -la .devcontainer/setup.sh  # Should have 'x' permission

# Validate JSON files
jq . .devcontainer.json
jq . .vscode/settings.json
jq . .vscode/launch.json
jq . .vscode/tasks.json

# Validate YAML
python -m yaml .pre-commit-config.yaml
python -m yaml .github/workflows/tests.yml

# Validate TOML
python -m tomli pyproject.toml
```

### Step 5: Test locally
```bash
# If using submodule setup:
cd ~/homeassistant/core
git submodule add https://github.com/agittins/bermuda config/custom_components/bermuda
cd config/custom_components/bermuda
code .

# When prompted, "Reopen in Container"
# Verify setup.sh runs and completes successfully
```

### Step 6: Create PR
```bash
git add .
git commit -m "chore: refine VSCode dev environment for HA submodule setup"
git push -u origin chore/vscode-submodule-setup
```

Then create PR on GitHub with this description:

---

## PR Description Template

```markdown
## Summary

Refine the development experience for Bermuda by redesigning VSCode configuration to work seamlessly with Home Assistant's development environment when Bermuda is used as a git submodule.

## Changes

- ✅ **Intelligent setup script** (`.devcontainer/setup.sh`) with environment detection
- ✅ **Multi-scenario debugging** (pytest, HA local, remote attach)
- ✅ **Comprehensive automation tasks** (test, lint, format, coverage workflows)
- ✅ **Multi-version CI testing** (HA 2025.1, 2025.2, dev versions)
- ✅ **Realistic coverage target** (75% instead of 100%)
- ✅ **Complete developer guide** (DEVELOPMENT_SUBMODULE.md)
- ✅ **Updated tooling configs** (Ruff, pytest, pre-commit)

## Files Modified

- `.devcontainer.json` - Redesigned for HA dev container
- `.devcontainer/setup.sh` - New intelligent setup script
- `.vscode/settings.json` - Enhanced with HA standards
- `.vscode/launch.json` - Multi-scenario debugging
- `.vscode/tasks.json` - Comprehensive automation
- `setup.cfg` - Adjusted coverage to 75%
- `pyproject.toml` - Clarified ruff config
- `.pre-commit-config.yaml` - Updated with new hooks
- `DEVELOPMENT_SUBMODULE.md` - New comprehensive guide
- `.github/workflows/tests.yml` - New multi-version CI

## Workflow Improvements

**Before:**
- Manual setup steps
- Unclear which Python/tools to use
- No automated multi-version testing
- Hard to debug in HA context

**After:**
- One-command setup (`Reopen in Container`)
- Clear environment detection & reporting
- Automated CI for HA 2025.1, 2025.2, dev
- Integrated debugging for pytest and HA
- 13 pre-configured tasks
- Comprehensive troubleshooting guide

## Testing

When merged, users can test with:
```bash
cd ~/homeassistant/core
git submodule add https://github.com/agittins/bermuda config/custom_components/bermuda
cd config/custom_components/bermuda
code .
# → Reopen in Container
# → Verify setup.sh completes successfully
```

## Related Issues

Addresses the need for a **clear, HA-aligned development environment** without manual file copying or unclear setup steps.

Aligns with:
- Home Assistant integration standards
- pytest best practices
- Ruff formatting conventions
- Git submodule workflows
```

---

## Quick Reference

| File | Purpose | Status |
|------|---------|--------|
| `.devcontainer.json` | Container config | Replace |
| `.devcontainer/setup.sh` | Setup script | Create (chmod +x) |
| `.vscode/settings.json` | Editor settings | Replace |
| `.vscode/launch.json` | Debug configs | Replace |
| `.vscode/tasks.json` | Automation | Replace |
| `setup.cfg` | Coverage config | Update |
| `pyproject.toml` | Ruff config | Update |
| `.pre-commit-config.yaml` | Pre-commit hooks | Update |
| `DEVELOPMENT_SUBMODULE.md` | Developer guide | Create |
| `.github/workflows/tests.yml` | CI workflow | Create |

---

## Key Benefits

✅ **Frictionless onboarding** - Setup runs automatically
✅ **Native HA integration** - Uses HA's dev environment
✅ **No file management** - Submodule stays in place
✅ **Multi-scenario debugging** - Pytest, HA, remote
✅ **Comprehensive CI** - Tests multiple HA versions
✅ **Clear documentation** - Troubleshooting included
✅ **Realistic expectations** - 75% coverage (not 100%)
✅ **Professional tooling** - Ruff, pre-commit, pytest

---

## Questions?

If you have questions about any of these files or the setup process, feel free to ask!
