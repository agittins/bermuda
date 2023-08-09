# Contribution guidelines

Contributing to this project should be as easy and transparent as possible, whether it's:

- Reporting a bug
- Discussing the current state of the code
- Submitting a fix
- Proposing new features

## Github is used for everything

Github is used to host code, to track issues and feature requests, as well as accept pull requests.

Pull requests are the best way to propose changes to the codebase.

1. Fork the repo and create your branch from `master`.
2. If you've changed something, update the documentation.
3. Make sure your code lints (using black).
4. Test you contribution.
5. Issue that pull request!

## Any contributions you make will be under the MIT Software License

In short, when you submit code changes, your submissions are understood to be under the same [MIT License](http://choosealicense.com/licenses/mit/) that covers the project. Feel free to contact the maintainers if that's a concern.

## Report bugs using Github's [issues](../../issues)

GitHub issues are used to track public bugs.
Report a bug by [opening a new issue](../../issues/new/choose); it's that easy!

## Write bug reports with detail, background, and sample code

**Great Bug Reports** tend to have:

- A quick summary and/or background
- Steps to reproduce
  - Be specific!
  - Give sample code if you can.
- What you expected would happen
- What actually happens
- Notes (possibly including why you think this might be happening, or stuff you tried that didn't work)

People _love_ thorough bug reports. I'm not even kidding.

## Use a Consistent Coding Style

Use [black](https://github.com/ambv/black) and [prettier](https://prettier.io/)
to make sure the code follows the style.

Or use the `pre-commit` settings implemented in this repository
(see deicated section below).

## Test your code modification

This custom component is based on [integration_blueprint template](https://github.com/custom-components/integration_blueprint).

It comes with development environment in a container, easy to launch
if you use Visual Studio Code. With this container you will have a stand alone
Home Assistant instance running and already configured with the included
[`.devcontainer.json`](./.devcontainer.json)
file.

You can use the `pre-commit` settings implemented in this repository to have
linting tool checking your contributions (see dedicated section below).

You should also verify that existing [tests](./tests) are still working
and you are encouraged to add new ones.
You can run the tests using the following command from the root folder:

`./scripts/test`

If any of the tests fail, make the necessary changes to the tests as part of
your changes to the integration.

## Pre-commit

You can use the [pre-commit](https://pre-commit.com/) settings included in the
repostory to have code style and linting checks.

With `pre-commit` tool already installed,
activate the settings of the repository:

```console
$ pre-commit install
```

Now the pre-commit tests will be done every time you commit.

You can run the tests on all repository file with the command:

```console
$ pre-commit run --all-files
```

## License

By contributing, you agree that your contributions will be licensed under its MIT License.
