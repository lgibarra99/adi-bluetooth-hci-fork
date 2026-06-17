#!/usr/bin/env python3
###############################################################################
#
# Copyright 2026 Analog Devices, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
##############################################################################
"""
bt_app.py

Description:
    General-purpose Bluetooth Classic application framework using raw HCI commands.
    Provides a base class for building BR/EDR applications with configurable
    device name, Class of Device, and scan settings.

Usage:
    python3 bt_app.py [port]

Example:
    python3 bt_app.py /dev/ttyUSB0

Features:
    - Controller initialization and capability queries
    - BR/EDR configuration (SSP, CoD, scan settings)
    - Flexible device configuration (name, class, timeouts)
    - Extended Inquiry Response (EIR) support
    - Connectable and/or discoverable modes
    - Device Under Test (DUT) mode for Bluetooth testing

Known Limitations (Controller Firmware Bugs in UART Mode):
    - Write Local Name (0x0C13) is DISABLED - data loss bug (~15 bytes lost)
    - Write Inquiry Scan Activity (0x0C43) is DISABLED - no response sent
    - Write Extended Inquiry Response (0x0C52) is DISABLED by default - data loss
      bug (~23 bytes lost). Device name cannot be advertised in UART mode.
    - Use --enable-eir flag to attempt EIR (will likely timeout)
    - See HCI_Write_Local_Name_UART_Bug_Analysis.md for detailed analysis
"""

import sys
import time
import argparse
from max_ble_hci import BleHci
from max_ble_hci.hci_packets import CommandPacket
from max_ble_hci.packet_defs import OGF

# HCI Opcode Group Fields
OGF_LINK_CONTROL = 0x01     # Link Control commands
OGF_LINK_POLICY = 0x02      # Link Policy commands
OGF_CONTROLLER = 0x03       # Controller & Baseband commands
OGF_INFORMATIONAL = 0x04    # Informational parameters
OGF_STATUS = 0x05           # Status parameters
OGF_TESTING = 0x06          # Testing commands
OGF_LE_CONTROLLER = 0x08    # LE Controller commands

# Controller/Baseband OCF values (OGF=0x03)
OCF_WRITE_SCAN_ENABLE = 0x001A          # 0x0C1A
OCF_WRITE_LOCAL_NAME = 0x0013           # 0x0C13
OCF_WRITE_CLASS_OF_DEVICE = 0x0024      # 0x0C24
OCF_READ_CLASS_OF_DEVICE = 0x0023       # 0x0C23
OCF_WRITE_PAGE_TIMEOUT = 0x0018         # 0x0C18
OCF_WRITE_INQUIRY_MODE = 0x0045         # 0x0C45
OCF_WRITE_SSP_MODE = 0x0056             # 0x0C56
OCF_WRITE_CURRENT_IAC_LAP = 0x003A      # 0x0C3A
OCF_WRITE_EXTENDED_INQUIRY_RESPONSE = 0x0052  # 0x0C52
OCF_WRITE_INQUIRY_SCAN_ACTIVITY = 0x0043      # 0x0C43

# Informational OCF values (OGF=0x04)
OCF_READ_LOCAL_VERSION = 0x0001         # 0x1001
OCF_READ_LOCAL_COMMANDS = 0x0002        # 0x1002
OCF_READ_LOCAL_FEATURES = 0x0003        # 0x1003
OCF_READ_BUFFER_SIZE = 0x0005           # 0x1005
OCF_READ_BD_ADDR = 0x0009               # 0x1009

# Testing OCF values (OGF=0x06)
OCF_ENABLE_DUT_MODE = 0x0003            # 0x1803

# Scan enable modes
SCAN_DISABLED = 0x00
SCAN_INQUIRY = 0x01               # Discoverable
SCAN_PAGE = 0x02                  # Connectable
SCAN_INQUIRY_AND_PAGE = 0x03      # Both

# IAC LAP values
GIAC_LAP = 0x9E8B33  # General Inquiry Access Code
LIAC_LAP = 0x9E8B00  # Limited Inquiry Access Code

# Common Class of Device values
COD_COMPUTER = 0x000100          # Computer (unclassified)
COD_PHONE = 0x000200             # Phone (cellular, cordless, etc.)
COD_AUDIO = 0x000400             # Audio/Video device
COD_AUDIO_HEADSET = 0x000404     # Audio: Headset
COD_AUDIO_HANDSFREE = 0x000408   # Audio: Hands-free
COD_AUDIO_MICROPHONE = 0x000410  # Audio: Microphone
COD_AUDIO_SPEAKER = 0x000414     # Audio: Loudspeaker
COD_AUDIO_HEADPHONES = 0x000418  # Audio: Headphones

# Default configuration
DEFAULT_DEVICE_NAME = "BT_CLASSIC_DUT"
DEFAULT_CLASS_OF_DEVICE = 0x000100  # Computer (unclassified)
DEFAULT_PAGE_TIMEOUT = 0x6000       # ~15 seconds


class BluetoothClassicApp:
    """Base class for Bluetooth Classic applications using raw HCI commands.

    This class provides the fundamental BR/EDR setup and configuration
    capabilities. Inherit from this class to create specific applications
    (e.g., Handsfree, A2DP, HID, etc.).

    Attributes
    ----------
    controller : BleHci
        HCI controller interface
    device_name : str
        Local device name (set via EIR)
    cod : int
        Class of Device (24-bit value)
    page_timeout : int
        Page timeout in 0.625ms units
    """

    def __init__(self, port, baud=921600, device_name=None, class_of_device=None):
        """Initialize Bluetooth Classic application.

        Parameters
        ----------
        port : str
            Serial port path (e.g., "/dev/ttyUSB0")
        baud : int, optional
            Baud rate (default: 921600 for Hudson controller)
        device_name : str, optional
            Device name (default: "BT_CLASSIC_DUT")
        class_of_device : int, optional
            24-bit Class of Device value (default: 0x000100 = Computer)
        """
        print(f"Connecting to controller on {port} at {baud} baud...")
        self.controller = BleHci(port, baud=baud, id_tag="BT-APP", flowcontrol=True)
        self.device_name = device_name or DEFAULT_DEVICE_NAME
        self.cod = class_of_device if class_of_device is not None else DEFAULT_CLASS_OF_DEVICE
        self.page_timeout = DEFAULT_PAGE_TIMEOUT

    def flush_serial(self):
        """Flush any pending data from serial buffers.

        The controller sometimes sends spurious events (like Connection Complete)
        that need to be drained before sending the next command.
        """
        try:
            if hasattr(self.controller, 'port') and hasattr(self.controller.port, '_uart'):
                uart = self.controller.port._uart
                if hasattr(uart, 'reset_input_buffer'):
                    uart.reset_input_buffer()
                if hasattr(uart, 'reset_output_buffer'):
                    uart.reset_output_buffer()
        except Exception as e:
            print(f"  DEBUG: Could not flush serial buffers: {e}")

    # =========================================================================
    # Basic HCI Commands
    # =========================================================================

    def reset(self):
        """Reset the controller.

        Returns
        -------
        event
            HCI Command Complete event
        """
        print("\n=== HCI_Reset ===")
        event = self.controller.reset()
        print(f"Reset response: {event}")
        time.sleep(0.1)
        return event

    # =========================================================================
    # Informational Commands (Read Capabilities)
    # =========================================================================

    def read_local_version(self):
        """Read local version information.

        Returns
        -------
        event
            HCI Command Complete event with version info
        """
        print("\n=== Read Local Version Information ===")
        cmd = CommandPacket(OGF_INFORMATIONAL, OCF_READ_LOCAL_VERSION)
        event = self.controller.port.send_command(cmd)
        print(f"Version: {event}")
        return event

    def read_local_supported_features(self):
        """Read local supported features (LMP features).

        Returns
        -------
        event
            HCI Command Complete event with feature mask
        """
        print("\n=== Read Local Supported Features ===")
        cmd = CommandPacket(OGF_INFORMATIONAL, OCF_READ_LOCAL_FEATURES)
        event = self.controller.port.send_command(cmd)
        print(f"Features: {event}")
        return event

    def read_local_supported_commands(self):
        """Read supported HCI commands bitmap.

        Returns
        -------
        event
            HCI Command Complete event with commands bitmap
        """
        print("\n=== Read Local Supported Commands ===")
        cmd = CommandPacket(OGF_INFORMATIONAL, OCF_READ_LOCAL_COMMANDS)
        event = self.controller.port.send_command(cmd)
        print(f"Supported commands: {event}")
        return event

    def read_buffer_size(self):
        """Read ACL/SCO buffer sizes.

        Returns
        -------
        event
            HCI Command Complete event with buffer sizes
        """
        print("\n=== Read Buffer Size ===")
        cmd = CommandPacket(OGF_INFORMATIONAL, OCF_READ_BUFFER_SIZE)
        event = self.controller.port.send_command(cmd)
        print(f"Buffer sizes: {event}")
        return event

    def read_bd_addr(self):
        """Read BD_ADDR of the controller.

        Returns
        -------
        event
            HCI Command Complete event with BD_ADDR
        """
        print("\n=== Read BD_ADDR ===")
        cmd = CommandPacket(OGF_INFORMATIONAL, OCF_READ_BD_ADDR)
        event = self.controller.port.send_command(cmd)
        print(f"BD_ADDR: {event}")
        return event

    # =========================================================================
    # Controller Configuration Commands
    # =========================================================================

    def write_ssp_mode(self, enable=True):
        """Enable/disable Secure Simple Pairing (SSP).

        Parameters
        ----------
        enable : bool
            True to enable SSP, False to disable

        Returns
        -------
        event
            HCI Command Complete event
        """
        print(f"\n=== Write Simple Pairing Mode (enable={enable}) ===")
        mode = 0x01 if enable else 0x00
        cmd = CommandPacket(OGF_CONTROLLER, OCF_WRITE_SSP_MODE, params=[mode])
        event = self.controller.port.send_command(cmd)
        print(f"SSP mode response: {event}")
        return event

    def write_class_of_device(self, cod=None):
        """Set Class of Device (CoD).

        Parameters
        ----------
        cod : int, optional
            24-bit Class of Device value (uses self.cod if None)

        Returns
        -------
        event
            HCI Command Complete event

        Examples
        --------
        >>> app.write_class_of_device(COD_AUDIO_HANDSFREE)  # 0x000408
        >>> app.write_class_of_device(0x200408)  # Audio + Service class bits
        """
        if cod is None:
            cod = self.cod

        print(f"\n=== Write Class of Device (0x{cod:06x}) ===")

        # Split 24-bit CoD into 3 bytes (little-endian)
        params = [
            (cod >> 0) & 0xFF,
            (cod >> 8) & 0xFF,
            (cod >> 16) & 0xFF,
        ]

        cmd = CommandPacket(OGF_CONTROLLER, OCF_WRITE_CLASS_OF_DEVICE, params=params)
        event = self.controller.port.send_command(cmd)
        print(f"CoD response: {event}")
        return event

    def write_page_timeout(self, timeout=None):
        """Set page timeout.

        Parameters
        ----------
        timeout : int, optional
            Page timeout in 0.625ms units (uses self.page_timeout if None)

        Returns
        -------
        event
            HCI Command Complete event
        """
        if timeout is None:
            timeout = self.page_timeout

        print(f"\n=== Write Page Timeout (0x{timeout:04x} = {timeout * 0.625:.1f}ms) ===")

        # Split 16-bit timeout into 2 bytes (little-endian)
        params = [
            (timeout >> 0) & 0xFF,
            (timeout >> 8) & 0xFF,
        ]

        cmd = CommandPacket(OGF_CONTROLLER, OCF_WRITE_PAGE_TIMEOUT, params=params)
        event = self.controller.port.send_command(cmd)
        print(f"Page timeout response: {event}")
        return event

    def write_local_name(self, name=None):
        """Set local device name (DISABLED - see note).

        **WARNING**: This command is DISABLED due to a controller firmware bug
        in UART mode. The controller hangs waiting for "missing" bytes due to
        ring buffer accounting errors. Use write_extended_inquiry_response()
        to set the device name instead.

        Parameters
        ----------
        name : str, optional
            Device name (max 248 bytes)

        Raises
        ------
        NotImplementedError
            Always raises - command is disabled
        """
        raise NotImplementedError(
            "HCI_Write_Local_Name is DISABLED due to controller firmware bug in UART mode.\n"
            "Symptom: Controller receives 237 bytes but expects 248, hangs indefinitely.\n"
            "Workaround: Use write_extended_inquiry_response() to set device name via EIR.\n"
            "See HCI_Write_Local_Name_UART_Bug_Analysis.md for details."
        )

    def write_current_iac_lap(self, lap=GIAC_LAP):
        """Write Inquiry Access Code (IAC) LAP.

        Parameters
        ----------
        lap : int
            LAP value (0x9E8B33 for GIAC, 0x9E8B00 for LIAC)

        Returns
        -------
        event
            HCI Command Complete event
        """
        lap_type = "GIAC" if lap == GIAC_LAP else "LIAC" if lap == LIAC_LAP else f"0x{lap:06x}"
        print(f"\n=== Write Current IAC LAP ({lap_type}) ===")

        # Format: num_current_iac (1 byte) + LAP (3 bytes, little-endian)
        params = [
            0x01,  # num_current_iac
            (lap >> 0) & 0xFF,
            (lap >> 8) & 0xFF,
            (lap >> 16) & 0xFF,
        ]

        cmd = CommandPacket(OGF_CONTROLLER, OCF_WRITE_CURRENT_IAC_LAP, params=params)
        event = self.controller.port.send_command(cmd)
        print(f"IAC LAP response: {event}")
        return event

    def write_inquiry_scan_activity(self, interval=0x0800, window=0x0012):
        """Write inquiry scan activity parameters (DISABLED - see note).

        **WARNING**: This command is DISABLED due to a controller firmware bug
        in UART mode. The controller receives the command but never sends a
        response, causing timeout. Default scan parameters will be used.

        Parameters
        ----------
        interval : int
            Inquiry scan interval (in 0.625ms units)
        window : int
            Inquiry scan window (in 0.625ms units)

        Raises
        ------
        NotImplementedError
            Always raises - command is disabled
        """
        raise NotImplementedError(
            "HCI_Write_Inquiry_Scan_Activity is DISABLED due to controller firmware bug in UART mode.\n"
            "Symptom: Controller receives command but never sends response, causing timeout.\n"
            "Workaround: Use default scan parameters.\n"
            "See HCI_Write_Local_Name_UART_Bug_Analysis.md for details."
        )

    def write_extended_inquiry_response(self, eir_data=None, device_name=None):
        """Write Extended Inquiry Response (EIR) data.

        **WARNING**: This command also suffers from the UART mode data loss bug.
        Large payloads (241 bytes) lose ~23 bytes during UART reception, causing
        the controller to hang. This command is DISABLED by default in bt_app.py.

        Use --enable-eir flag to attempt (will likely timeout in UART mode).

        Parameters
        ----------
        eir_data : list of int, optional
            EIR data (240 bytes). If None, creates minimal EIR with device name.
        device_name : str, optional
            Device name to include in EIR (uses self.device_name if None)

        Returns
        -------
        event
            HCI Command Complete event

        Raises
        ------
        TimeoutError
            In UART mode due to data loss bug
        """
        if device_name is None:
            device_name = self.device_name

        print(f"\n=== Write Extended Inquiry Response ===")

        if eir_data is None:
            # Create minimal EIR with complete local name
            name_bytes = device_name.encode('utf-8')
            name_len = len(name_bytes)

            # EIR format: length, type, data
            eir_data = [
                name_len + 1,  # Length (name + type byte)
                0x09,          # Complete Local Name (EIR data type)
            ] + list(name_bytes)

            # Pad to 240 bytes
            eir_data += [0] * (240 - len(eir_data))

        # First byte is FEC_Required (0x00 = not required)
        params = [0x00] + eir_data[:240]

        cmd = CommandPacket(OGF_CONTROLLER, OCF_WRITE_EXTENDED_INQUIRY_RESPONSE, params=params)
        event = self.controller.port.send_command(cmd, timeout=5.0)  # Longer timeout for 241-byte payload
        print(f"EIR response: {event}")
        return event

    def write_scan_enable(self, mode):
        """Enable/disable inquiry and page scans.

        Parameters
        ----------
        mode : int
            Scan mode:
            - SCAN_DISABLED (0x00): Not discoverable or connectable
            - SCAN_INQUIRY (0x01): Discoverable only
            - SCAN_PAGE (0x02): Connectable only
            - SCAN_INQUIRY_AND_PAGE (0x03): Discoverable and connectable

        Returns
        -------
        event
            HCI Command Complete event
        """
        mode_names = {
            SCAN_DISABLED: "DISABLED",
            SCAN_INQUIRY: "INQUIRY (discoverable only)",
            SCAN_PAGE: "PAGE (connectable only)",
            SCAN_INQUIRY_AND_PAGE: "INQUIRY+PAGE (discoverable+connectable)"
        }
        mode_str = mode_names.get(mode, f"UNKNOWN(0x{mode:02x})")

        print(f"\n=== Write Scan Enable ({mode_str}) ===")
        cmd = CommandPacket(OGF_CONTROLLER, OCF_WRITE_SCAN_ENABLE, params=[mode])
        event = self.controller.port.send_command(cmd)
        print(f"Scan enable response: {event}")
        return event

    def enable_dut_mode(self):
        """Enable Device Under Test (DUT) mode.

        This command enables the Device Under Test mode for Bluetooth testing.
        When DUT mode is enabled, the controller enters a special test mode
        that allows various RF test operations.

        **WARNING**: Enabling DUT mode typically disables normal Bluetooth
        operations. This command should only be used for testing purposes.

        The DUT mode is commonly used for:
        - Bluetooth qualification testing
        - RF characterization
        - Production line testing
        - Regulatory compliance testing

        Returns
        -------
        event
            HCI Command Complete event

        Note
        ----
        After enabling DUT mode, the controller may require a reset to return
        to normal operation mode.
        """
        print("\n=== Enable Device Under Test (DUT) Mode ===")
        print("WARNING: This will put the device into test mode")
        print("         Normal Bluetooth operations may be disabled")

        cmd = CommandPacket(OGF_TESTING, OCF_ENABLE_DUT_MODE, params=[])
        event = self.controller.port.send_command(cmd)
        print(f"DUT mode response: {event}")

        print("\nDUT mode enabled. Device is now in test mode.")
        return event

    # =========================================================================
    # High-Level Initialization
    # =========================================================================

    def read_controller_info(self):
        """Read all controller information and capabilities.

        This is typically the first step after reset.
        """
        print("\n" + "=" * 70)
        print("Reading Controller Information")
        print("=" * 70)

        self.read_local_version()
        self.read_local_supported_features()
        self.read_local_supported_commands()
        self.read_buffer_size()
        self.read_bd_addr()

    def configure_br_edr(self, enable_ssp=True):
        """Configure basic BR/EDR settings.

        Parameters
        ----------
        enable_ssp : bool, optional
            Enable Secure Simple Pairing (default: True)
        """
        print("\n" + "=" * 70)
        print("Configuring BR/EDR Settings")
        print("=" * 70)

        self.write_ssp_mode(enable=enable_ssp)
        self.write_class_of_device()
        self.write_page_timeout()

    def setup_discoverability(self, device_name=None, enable_eir=False):
        """Setup device discoverability with optional EIR.

        Parameters
        ----------
        device_name : str, optional
            Device name for EIR (uses self.device_name if None)
        enable_eir : bool, optional
            Enable Extended Inquiry Response (default: False due to UART bug)
        """
        print("\n" + "=" * 70)
        print("Setting Up Discoverability")
        print("=" * 70)

        self.write_current_iac_lap()
        time.sleep(0.5)  # Controller may send spurious events
        self.flush_serial()

        if enable_eir:
            print("\nWARNING: EIR enabled - this may fail in UART mode due to data loss bug")
            try:
                self.write_extended_inquiry_response(device_name=device_name)
                time.sleep(0.2)
            except Exception as e:
                print(f"\nERROR: EIR command failed (expected in UART mode): {e}")
                print("Continuing without EIR - device name will not be advertised")
        else:
            print("\nNOTE: EIR disabled - device name will not be advertised via Extended Inquiry Response")
            print("      (EIR disabled by default due to UART mode data loss bug)")
            print("      Use --enable-eir flag to attempt EIR (may timeout)")

    def initialize(self, discoverable=True, connectable=True, enable_eir=None, enable_dut=False):
        """Run complete initialization sequence for Bluetooth Classic device.

        Parameters
        ----------
        discoverable : bool, optional
            Enable inquiry scan (default: True)
        connectable : bool, optional
            Enable page scan (default: True)
        enable_eir : bool, optional
            Enable Extended Inquiry Response (default: None, uses self.enable_eir if available)
        enable_dut : bool, optional
            Enable Device Under Test mode after initialization (default: False)

        Note
        ----
        If enable_dut is True, the device will enter DUT mode after basic setup.
        This may disable normal Bluetooth operations and is intended for testing only.
        """
        print("=" * 70)
        print("Bluetooth Classic Application Initialization")
        print("=" * 70)

        # Phase 1: Reset and read capabilities
        self.reset()
        self.read_controller_info()

        # Phase 2: Configure BR/EDR settings
        self.configure_br_edr()

        # Phase 3: Setup discoverability
        # Check if enable_eir is set via command-line flag
        if enable_eir is None:
            enable_eir = getattr(self, 'enable_eir', False)
        self.setup_discoverability(enable_eir=enable_eir)

        # Phase 4: Enable scans
        scan_mode = SCAN_DISABLED
        if discoverable and connectable:
            scan_mode = SCAN_INQUIRY_AND_PAGE
        elif discoverable:
            scan_mode = SCAN_INQUIRY
        elif connectable:
            scan_mode = SCAN_PAGE

        self.write_scan_enable(scan_mode)

        print("\n" + "=" * 70)
        print("Initialization Complete!")
        print("=" * 70)
        self.print_status(discoverable, connectable)

        # Phase 5: Enable DUT mode if requested
        if enable_dut:
            print("\n" + "=" * 70)
            print("Enabling DUT Mode (as requested)")
            print("=" * 70)
            self.enable_dut_mode()
            print("\nNOTE: Device is now in test mode. Normal operations may be unavailable.")
            print("      Reset the controller to exit DUT mode.")

    def print_status(self, discoverable=True, connectable=True):
        """Print current device status.

        Parameters
        ----------
        discoverable : bool
            Whether device is discoverable
        connectable : bool
            Whether device is connectable
        """
        print(f"\nDevice Status:")
        print(f"  - Name: {self.device_name}")
        print(f"  - Class of Device: 0x{self.cod:06x}")
        print(f"  - Discoverable: {'Yes' if discoverable else 'No'}")
        print(f"  - Connectable: {'Yes' if connectable else 'No'}")


def main():
    """Main entry point for demo application."""
    parser = argparse.ArgumentParser(
        description='General-purpose Bluetooth Classic application using raw HCI commands',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 bt_app.py /dev/ttyUSB0
  python3 bt_app.py /dev/ttyUSB0 --device_name "My_BT_Device"
  python3 bt_app.py /dev/serial/by-id/usb-FTDI_FT232R_USB_UART_00000000-if00-port0

Note:
  Hudson controller uses 921600 baud with hardware flow control (enabled by default)
        """
    )

    parser.add_argument(
        'port',
        help='Serial port path (e.g., /dev/ttyUSB0)'
    )

    parser.add_argument(
        '--device_name',
        default='GRANITE_DUT',
        help='Device name to advertise via EIR (default: GRANITE_DUT)'
    )

    parser.add_argument(
        '--class_of_device',
        type=lambda x: int(x, 0),  # Accepts decimal or hex (0x...)
        default=COD_COMPUTER,
        help='Class of Device in hex (default: 0x000100 = Computer)'
    )

    parser.add_argument(
        '--baud',
        type=int,
        default=921600,
        help='UART baud rate (default: 921600)'
    )

    parser.add_argument(
        '--enable-eir',
        action='store_true',
        help='Enable Extended Inquiry Response (WARNING: may fail in UART mode)'
    )

    parser.add_argument(
        '--enable-dut',
        action='store_true',
        help='Enable Device Under Test mode after initialization (for testing only)'
    )

    args = parser.parse_args()

    try:
        # Create Bluetooth Classic application
        app = BluetoothClassicApp(
            args.port,
            baud=args.baud,
            device_name=args.device_name,
            class_of_device=args.class_of_device
        )

        # Store enable_eir flag for initialization
        app.enable_eir = args.enable_eir

        # Run initialization sequence
        app.initialize(discoverable=True, connectable=True, enable_dut=args.enable_dut)

        print("\nPress Ctrl+C to exit")

        # Keep running to maintain the state
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\nShutting down...")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
