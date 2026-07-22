# Contributing to Summy AI Gateway

Thank you for your interest in contributing to Summy AI Gateway! This document provides guidelines and instructions for contributing.

## Code of Conduct

Please read and follow our [Code of Conduct](CODE_OF_CONDUCT.md) to maintain a welcoming and inclusive community.

## How Can I Contribute?

### Reporting Bugs

Before creating bug reports, please check the existing issues as you might find out that you don't need to create one. When you are creating a bug report, please include as many details as possible:

* **Use a clear and descriptive title**
* **Describe the exact steps to reproduce the problem**
* **Provide specific examples to demonstrate the steps**
* **Describe the behavior you observed and what behavior you expected**
* **Include screenshots if possible**
* **Include system information** (OS, Python version, Docker version, etc.)

### Suggesting Enhancements

Enhancement suggestions are tracked as GitHub issues. When creating an enhancement suggestion, please include:

* **Use a clear and descriptive title**
* **Provide a detailed description of the suggested enhancement**
* **Explain why this enhancement would be useful**
* **List some examples of how this enhancement would be used**

### Pull Requests

* Fill in the required template
* Follow the Python style guide (PEP 8)
* Include tests for new features
* Update documentation as needed
* Ensure all tests pass before submitting
* Keep pull requests focused on a single feature or fix

## Development Setup

### Prerequisites

* Python 3.9+
* Docker and Docker Compose
* Git

### Local Development

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/YOUR_USERNAME/Summy.git
   cd Summy
   ```

3. Create a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

4. Install dependencies:
   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt  # For development tools
   ```

5. Run the application locally:
   ```bash
   python app.py
   ```

6. Run tests:
   ```bash
   pytest
   ```

## Coding Guidelines

### Python Style Guide

* Follow PEP 8 conventions
* Use type hints where possible
* Write docstrings for public functions and classes
* Keep functions small and focused
* Use meaningful variable names

### Code Organization

```
src/
├── __init__.py          # Package initialization
├── gateway.py           # Main HTTP gateway
├── warden.py            # Resource monitoring
├── pipeline_optimizer.py # Model routing logic
├── memory_loader.py     # Configuration server
└── traffic_shaper.py    # Rate limiting
```

### Testing

* Write unit tests for new features
* Aim for at least 80% code coverage
* Use pytest fixtures for common setup
* Mock external services in tests

Example test:
```python
async def test_gateway_health():
    gateway = MultiplexingGateway()
    response = await gateway.handle_health(None)
    assert response.status == 200
    data = await response.json()
    assert data['status'] == 'healthy'
```

### Documentation

* Update README.md for user-facing changes
* Add inline comments for complex logic
* Update API documentation for endpoint changes
* Include examples in docstrings

## Release Process

1. Bump version in `config/summy.yaml`
2. Update CHANGELOG.md
3. Create a pull request
4. Tag the release with semantic versioning (vX.Y.Z)
5. CI/CD will automatically build and publish

## Questions?

Feel free to open an issue for any questions or discussions.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
