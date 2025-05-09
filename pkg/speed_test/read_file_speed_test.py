"""File I/O Speed Test Module

This module provides functionality for measuring file I/O performance by:
- Walking through a directory tree recursively
- Reading each file and measuring read time and throughput
- Calculating aggregate statistics like:
  - Total data size processed
  - Total processing time
  - Average read speed in MB/s
  - Per-file metrics

The module is designed for:
- Benchmarking storage system performance
- Identifying slow file access patterns
- Validating I/O throughput requirements
"""

import os
import sys
import time

from pkg.utils import io  # Importing a custom utility module for getting the repository path


def measure_io_speed(directory_path):
    # Check if the provided path is a valid directory
    if not os.path.isdir(directory_path):
        print(f"Directory {directory_path} does not exist.")
        return

    # Initialize counters for total size, total time, and file count
    total_size = 0
    total_time = 0
    file_count = 0

    # Walk through the directory and all subdirectories
    for root, dirs, files in os.walk(directory_path):
        for file in files:
            # Get the full path of the current file
            file_path = os.path.join(root, file)
            # Get the size of the current file in bytes
            file_size = os.path.getsize(file_path)

            # Record the start time before reading the file
            start_time = time.time()
            with open(file_path, "rb") as f:
                _ = f.read()  # Read the entire file content into memory
            # Calculate the elapsed time after reading the file
            elapsed_time = time.time() - start_time

            # Update total size and time with the current file's size and reading time
            total_size += file_size
            total_time += elapsed_time
            file_count += 1

            # Print the size and time taken to read the current file
            print(f"Read {file} - Size: {file_size / (1024 * 1024):.2f} MB, Time: {elapsed_time:.4f} seconds")

    # If files were read, calculate and print the overall statistics
    if file_count > 0:
        # Calculate the average reading speed in MB/s
        average_speed = total_size / total_time / (1024 * 1024)
        print(f"\nTotal Files: {file_count}")
        print(f"Total Size: {total_size / (1024 * 1024):.2f} MB")
        print(f"Total Time: {total_time:.4f} seconds")
        print(f"Average Speed: {average_speed:.2f} MB/s")
    else:
        print("No files found in the specified directory.")


if __name__ == "__main__":
    # Get the absolute path of the current script
    cur_path = os.path.abspath(sys.argv[0])

    # Get the repository root path using a custom utility function
    repo_dir = io.get_repo_path(cur_path)

    # Define the directory to test the I/O speed
    directory_to_test = f"{repo_dir}/pkg/data/passive_biv/datasets/train"

    # Call the function to measure the I/O speed in the specified directory
    measure_io_speed(directory_to_test)
