#!/bin/bash
###############################################################################
# Quick script to install MAX-BLE-HCI in development mode
###############################################################################

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================================================"
echo "Installing MAX-BLE-HCI in Development Mode"
echo "========================================================================"
echo ""
echo "Package directory: $SCRIPT_DIR"
echo ""

# Check if pip is available
if ! command -v pip &> /dev/null && ! command -v pip3 &> /dev/null; then
    echo "❌ Error: pip is not installed"
    echo "   Please install pip first:"
    echo "   sudo apt install python3-pip"
    exit 1
fi

PIP_CMD="pip3"
if ! command -v pip3 &> /dev/null; then
    PIP_CMD="pip"
fi

echo "Using: $PIP_CMD"
echo ""

# Check if running in a virtual environment
if [ -n "$VIRTUAL_ENV" ]; then
    echo "✓ Virtual environment detected: $VIRTUAL_ENV"
    echo ""
elif python3 -c "import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)" 2>/dev/null; then
    echo "✓ Virtual environment detected"
    echo ""
else
    echo "⚠️  Warning: Not running in a virtual environment"
    echo "   The package will be installed in your global Python environment."
    echo "   Press Ctrl+C to cancel, or Enter to continue..."
    read
fi

# Install in editable mode
echo "Installing package in editable mode..."
cd "$SCRIPT_DIR"
$PIP_CMD install -e .

echo ""
echo "========================================================================"
echo "✓ Installation Complete!"
echo "========================================================================"
echo ""

# Verify installation
echo "Verifying installation..."
if python3 -c "from max_ble_hci import BleHci" 2>/dev/null; then
    echo "✓ Successfully imported max_ble_hci"
    echo ""

    # Show package location
    PACKAGE_LOC=$(python3 -c "import max_ble_hci; print(max_ble_hci.__file__)")
    echo "Package location: $PACKAGE_LOC"
    echo ""

    echo "You can now run the example scripts:"
    echo "  python3 examples/run_handsfree_by_hci.py /dev/ttyUSB0"
    echo "  python3 examples/connection.py"
    echo ""

    echo "Development mode benefits:"
    echo "  • Changes to source code are immediately active"
    echo "  • No need to reinstall after modifications"
    echo "  • Package is importable from anywhere"
else
    echo "❌ Error: Could not import max_ble_hci"
    echo "   Please check the error messages above"
    exit 1
fi

echo "========================================================================"
