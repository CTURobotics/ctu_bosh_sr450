#!/usr/bin/env python
#
# Copyright (c) CTU -- All Rights Reserved
# Created on: 2023-10-31
#     Author: Vladimir Petrik <vladimir.petrik@cvut.cz>
#
from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike

from ctu_mars_control_unit import MarsControlUnit


class RobotBosh:
    """RobotBosh is a class representing the Bosh SR450 robot. It has access to the
    robot's control unit and basic kinematics functions."""

    def __init__(self, tty_dev: str | None = "/dev/ttyUSB0", baudrate: int = 19200):
        super().__init__()
        self._mars = (
            MarsControlUnit(tty_dev=tty_dev, baudrate=baudrate)
            if tty_dev is not None
            else None
        )
        self.link_lengths = np.array([0.25, 0.2])

        # Constants for controlling the robot
        self._motors_ids = "ABCD"  # i.e. mars8 axes that are being controlled
        self._REGPWRON = 1  # user must press arm power button
        self._REGPWRFLG = 5
        self._REGCFG = [1498, 1498, 1370, 1498]
        self._REGME = [30000, 16000, 16000, 16000]
        self._REGP = [50, 50, 50, 50]
        self._REGI = [5, 5, 5, 5]
        self._REGD = [50, 50, 50, 50]
        self._IDLEREL = 0  # Bosch robot shall not release after some time

        # Constants for conversion between IRC and joint values
        self._q_to_q_irc = np.array(
            [54286.97186604, 35061.99184485, 444000.0, 22686.85977431]
        )

        lower_bound_irc = np.array([-80536, -73434, -146520, -7919])
        upper_bound_irc = np.array([80536, 73434, 2220, 427637])
        self.q_min = self._irc_to_joint_values(lower_bound_irc)
        self.q_max = self._irc_to_joint_values(upper_bound_irc)
        self.q_home = np.array([0.0, 0.0, 0.0, 0.0])

        # Speeds (IRC*256/msec)
        self._min_speed_irc256_per_ms = np.array([1000, 1000, 1000, 1000])
        self._max_speed_irc256_per_ms = np.array([10000, 10000, 5000, 5000])
        self._default_speed_irc256_per_ms = np.array([10000, 10000, 5000, 5000])

        # Accelerations
        self._min_acceleration_irc_per_ms = np.array([0, 0, 1, 1])
        self._max_acceleration_irc_per_ms = np.array([100, 100, 100, 100])
        self._default_acceleration_irc_per_ms = np.array([30, 30, 20, 30])

        self._initialized = False

    def release(self):
        """Release errors and reset control unit."""
        self._mars.send_cmd("RELEASE:\n")

    def reset_motors(self):
        """Reset motors of robot."""
        self._mars.send_cmd("PURGE:\n")

    def close(self):
        """Close connection to the robot."""
        self._mars.close_connection()

    def initialize(self):
        """Initialize communication with robot and set all necessary parameters.
        This command will perform following settings:
         - synchronize communication with mars control unit
         - send command to power motors, wait for user to power them
         - reset motors and wait for them to be ready
         - set PID control parameters, maximum speed and acceleration
         - set value for IDLE release
         - perform hard home and soft home
        """
        self._mars.sync_cmd_fifo()

        self._mars.send_cmd(f"REGPWRON:{self._REGPWRON}\n")
        input("Press ARM POWER button and then press enter to continue.")
        self._mars.send_cmd(f"REGPWRFLG:{self._REGPWRFLG}\n")

        print("Resetting motors")
        self._mars.send_cmd("PURGE:\n")
        self._mars.send_cmd("STOP:\n")
        assert self._mars.check_ready()
        self._mars.wait_ready()

        fields = ["REGME", "REGCFG", "REGP", "REGI", "REGD"]
        for f in fields:
            field_values = getattr(self, f"_{f}")
            assert field_values is not None
            assert len(field_values) == len(self._motors_ids)
            for motor_id, value in zip(self._motors_ids, field_values):
                self._mars.send_cmd(f"{f}{motor_id}:{value}\n")

        self.set_speed(self._default_speed_irc256_per_ms)
        self.set_acceleration(self._default_acceleration_irc_per_ms)

        self._mars.send_cmd(f"IDLEREL:{self._IDLEREL}\n")

        self.hard_home()
        self.soft_home()
        self._initialized = True

    def _joint_values_to_irc(self, joint_values: ArrayLike) -> np.ndarray:
        """Convert joint values [rad,m] to IRC."""
        j = np.asarray(joint_values)
        assert j.shape == (len(self._motors_ids),), "Incorrect number of joints."
        return np.rint(joint_values * self._q_to_q_irc)

    def _irc_to_joint_values(self, irc: ArrayLike) -> np.ndarray:
        """Convert IRC to joint values [rad,m]."""
        irc = np.asarray(irc)
        assert irc.shape == (len(self._motors_ids),), "Incorrect number of joints."
        return irc / self._q_to_q_irc

    def set_speed(self, speed_irc256_ms: ArrayLike):
        """Set speed for each motor in IRC*256/msec."""
        assert len(speed_irc256_ms) == len(self._motors_ids)
        for axis, speed in zip(self._motors_ids, speed_irc256_ms):
            self._mars.send_cmd(f"REGMS:{axis}:{np.rint(speed)}\n")

    def set_speed_relative(self, fraction: float):
        """Set speed for each motor in fraction (0-1) of maximum speed."""
        assert 0 <= fraction <= 1, "Fraction must be in [0,1]."
        s = self._min_speed_irc256_per_ms + fraction * (
            self._max_speed_irc256_per_ms - self._min_speed_irc256_per_ms
        )
        self.set_speed(s)

    def set_acceleration(self, acceleration_irc_ms: ArrayLike):
        """Set acceleration for each motor in IRC/msec."""
        assert len(acceleration_irc_ms) == len(self._motors_ids)
        for axis, acceleration in zip(self._motors_ids, acceleration_irc_ms):
            self._mars.send_cmd(f"REGACC:{axis}:{np.rint(acceleration)}\n")

    def set_acceleration_relative(self, fraction: float):
        """Set acceleration for each motor in fraction (0-1) of maximum acceleration."""
        assert 0 <= fraction <= 1, "Fraction must be in [0,1]."
        a = self._min_acceleration_irc_per_ms + fraction * (
            self._max_acceleration_irc_per_ms - self._min_acceleration_irc_per_ms
        )
        self.set_acceleration(a)

    def hard_home(self):
        """Perform hard home of the robot s.t. prismatic joint is homed first followed
        by joint A, B, and D. The speed is reset to default value before homing."""
        self.set_speed(self._default_speed_irc256_per_ms)
        self.set_acceleration(self._default_acceleration_irc_per_ms)
        for a in "CABD":
            self._mars.send_cmd("HH" + a + ":\n")
            self._mars.wait_ready()

    def soft_home(self):
        """Move robot to the home position using coordinated movement."""
        self._mars.setup_coordmv(self._motors_ids)
        self._mars.coordmv(self._joint_values_to_irc(self.q_home))
        self.wait_for_motion_stop()

    def move_to_q(self, q: ArrayLike):
        """Move robot to the given joint configuration using coordinated movement.
        The soft_home needs to be called before to set up coordinate movements."""
        assert self._initialized, "You need to initialize the robot before moving it."
        assert np.all(q >= self.q_min) and np.all(
            q <= self.q_max
        ), "Joint limits violated."
        self._mars.coordmv(self._joint_values_to_irc(q))

    def get_q(self) -> np.ndarray:
        """Get current joint configuration."""
        return self._irc_to_joint_values(
            self._mars.get_current_q_irc()[: len(self._motors_ids)]
        )

    def in_motion(self) -> bool:
        """Return whether the robot is in motion."""
        return self._mars.check_ready()

    def wait_for_motion_stop(self):
        """Wait until the robot stops moving."""
        self._mars.wait_ready()

    def fk(self, q: ArrayLike) -> tuple[float, float, float, float]:
        """Compute forward kinematics for the given joint configuration @param q.
        The output is (x,y,z,phi) where x,y,z are position of the end-effector w.r.t.
        the base frame and phi is the orientation of the end-effector w.r.t. the base.
        """
        pass

    def ik(
        self, x: float, y: float, z: float = 0.25, phi: float = 0
    ) -> list[np.ndarray]:
        """Compute IK s.t. the end-effector is at the given position w.r.t. the
        reference frame. The last joint value is set to the given fixed value. It does
        not influence solution of IK.
        Internally, :param z is used to compute third (prismatic) joint value and
        :param x and :param y are used to compute first and second (revolute) joint
        values.
        If no solution exists, return empty list.
        """
        # todo
        return []
