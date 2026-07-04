# Bermuda Development with Home Assistant (Submodule Setup)

This guide describes the **recommended setup** for Bermuda development, which treats Bermuda as a git submodule within the Home Assistant development environment.

## Why This Approach?

- ✅ **No file copying** — code lives in the right place from day one
- ✅ **Native HA environment** — uses HA's dev container and Python
- ✅ **Direct commits** — all changes stay in the Bermuda repo
- ✅ **Easy testing** — tests run against multiple HA versions via CI
- ✅ **Scalable** — works for single developers or larger teams

## Quick Start (2 minutes)

### If you already have `homeassistant/core`:

```bash
cd ~/projects/homeassistant/core
git submodule add https://github.com/agittins/bermuda config/custom_components/bermuda
cd config/custom_components/bermuda
code .
```

When prompted, click **"Reopen in Container"** (uses HA's devcontainer).

### Fresh setup:

```bash
git clone https://github.com/home-assistant/core.git ha-dev
cd ha-dev
git submodule add https://github.com/agittins/bermuda config/custom_components/bermuda
cd config/custom_components/bermuda
code .
```

Again, choose **"Reopen in Container"** when prompted.

## Directory Structure

After setup, you'll have:

```
~/projects/homeassistant/
  core/                                    # ← HA core repo
    .devcontainer.json                     # ← HA's container config
    config/
      custom_components/
        bermuda/                           # ← Your Bermuda submodule (this is `.`)
          .git (→ points to bermuda repo)
          custom_components/bermuda/       # ← Actual Bermuda code
          tests/
          .vscode/
          .devcontainer.json               # ← Bermuda-specific setup
          scripts/
          README.md
          ... (other Bermuda files)
```

## Environment Setup

When you open the Bermuda folder in VS Code:

1. Click **"Reopen in Container"**
2. VS Code uses **HA's dev container** (latest-dev image)
3. The `scripts/setup` script runs automatically:
   - Detects submodule setup ✓
   - Sets up Python venv
   - Installs test dependencies
   - Configures pre-commit hooks
   - Displays quick-start guide

Setup takes ~30 seconds on first run.

## Common Tasks

### Run Tests

```bash
# All tests
pytest tests/

# Tests with coverage report
pytest tests/ --cov

# Fast mode (exit on first failure)
pytest tests/ -x

# Specific file
pytest tests/test_config_flow.py -vv

# Specific test function
pytest tests/test_config_flow.py::test_form -vv
```

Use **Ctrl+Shift+D** in VS Code and select **"pytest: All Tests"** to debug.

### Format & Lint Code

```bash
# Check code style
ruff check .

# Auto-fix issues
ruff check . --fix

# Format code
ruff format .

# Do both
ruff check . --fix && ruff format .
```

Or use the **"ruff: Fix and format"** task in VS Code (Ctrl+Shift+B).

### Pre-commit Hooks

Hooks run automatically on commit, but you can run manually:

```bash
# All files
pre-commit run --all-files

# Only staged files
pre-commit run
```

Task in VS Code: **"pre-commit: Run all checks"** (Ctrl+Shift+B).

### Home Assistant Integration Testing

If you need to test Bermuda inside a running HA instance:

```bash
# Start HA on localhost:8123
hass -c ../../../../config --debug
```

Then:
1. Navigate to `http://localhost:8123`
2. Settings → Devices & Services → Create Integration
3. Search for "Bermuda BLE Trilateration"
4. Configure proxies and devices

Or use **"Home Assistant: Start"** task in VS Code (Ctrl+Shift+B).

## Debug Configurations

### Debug a test file

1. Open the test file
2. Press **F5** → Select **"pytest: All Tests"** or **"pytest: Current File"**

### Debug a specific test

1. Click on the test function name
2. Press **F5** → Select **"pytest: Current Test (cursor)"**

### Debug Home Assistant instance

1. Press **F5** → Select **"Home Assistant (local, if submodule setup)"**
2. HA will start with debugger enabled
3. Set breakpoints in Bermuda code
4. Interact with HA at `http://localhost:8123`

## Git Workflow

### Making Changes

```bash
# Edit files in Bermuda (they're in the right place!)
vim custom_components/bermuda/coordinator.py

# Commit to Bermuda repo
git add custom_components/bermuda/
git commit -m "feat: add feature X"

# Push to Bermuda repo
git push origin main
```

The submodule stays independent — commits go to Bermuda, not HA core.

### Updating Bermuda submodule

If the main Bermuda repo gets updates:

```bash
git submodule update --remote
```

Or use the **"Git: Update submodule"** task in VS Code.

## Troubleshooting

### Q: "Python not found" in VS Code

**A:** Ensure `.devcontainer.json` is present in the Bermuda folder. Restart container:
- Ctrl+Shift+P → "Dev Containers: Rebuild Container"

### Q: Tests fail with import errors

**A:** Verify dependencies:
```bash
pip install -r requirements_test.txt
```

### Q: Can't start HA because parent repo not found

**A:** This is OK! You can run tests without the parent repo. Full HA integration requires the parent `homeassistant/core`.

To test HA integration:
```bash
# Verify parent exists
ls ../../../../config/

# If missing, you're not in a submodule setup
# Go back and re-read "Quick Start" section
```

### Q: Pre-commit hooks won't install

**A:** Manually install:
```bash
pre-commit install --install-hooks
```

### Q: Large file changes aren't showing in git status

**A:** This is normal for submodules. Verify:
```bash
cd config/custom_components/bermuda
git status
```

## Development Best Practices

### Before committing:

1. **Run tests**
   ```bash
   pytest tests/ -q
   ```

2. **Check code style**
   ```bash
   ruff check . --fix
   ruff format .
   ```

3. **Run full check suite**
   - Task: **"Development: Full check suite"** (Ctrl+Shift+B)
   - Or manually: `pytest tests/ && ruff check . && pre-commit run --all-files`

### Code coverage

Generate coverage report:

```bash
pytest tests/ --cov=custom_components.bermuda --cov-report=html
```

Open `htmlcov/index.html` in browser to see gaps.

Current target: **75% coverage** (see `setup.cfg`).

## Workflow Examples

### Adding a new sensor

1. Create `custom_components/bermuda/new_sensor.py`
2. Add tests in `tests/test_new_sensor.py`
3. Run: `pytest tests/test_new_sensor.py -vv`
4. Format: `ruff format .`
5. Commit: `git commit -am "feat: add new sensor"`

### Fixing a bug

1. Create a test that reproduces the bug
2. Run: `pytest tests/ -x` (stops at first failure)
3. Fix the bug
4. Verify test passes
5. Commit: `git commit -am "fix: resolve bug in coordinator"`

### Preparing a PR

1. Create feature branch: `git checkout -b feature/my-feature`
2. Make changes
3. Run full check suite: `pre-commit run --all-files && pytest tests/`
4. Push: `git push -u origin feature/my-feature`
5. Open PR on GitHub

## Advanced: Testing Against Multiple HA Versions

The GitHub Actions CI tests against multiple HA versions (see `.github/workflows/tests.yml`). To test locally:

```bash
# This uses the current HA version in the container.
# For specific versions, see the CI workflow for pinning details.
pytest tests/ -vv
```

## File Reference

- `.devcontainer.json` — Container configuration
- `scripts/setup` — Environment setup script
- `.vscode/settings.json` — VS Code editor settings
- `.vscode/launch.json` — Debug configurations
- `.vscode/tasks.json` — Automation tasks
- `.pre-commit-config.yaml` — Pre-commit hooks
- `pyproject.toml` — Ruff and pytest config
- `setup.cfg` — Coverage and additional tool config
- `requirements_test.txt` — Test dependencies

## Getting Help

- **Setup issues** → Run `scripts/setup` manually to see errors
- **Test failures** → Add `-vv` to pytest: `pytest tests/test_name.py -vv`
- **Linting issues** → Run `ruff check . --show-fixes`
- **Questions** → Check [CONTRIBUTING.md](CONTRIBUTING.md) or open a GitHub discussion

---

**Happy coding! 🎉**
