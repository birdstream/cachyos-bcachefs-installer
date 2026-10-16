

https://github.com/user-attachments/assets/e43be8b9-1085-4726-9276-291f362b5ee1

# cachyos-bcachefs-installer

**[Experimental] CachyOS ISO remaster script with bcachefs installation and SSD/HDD tiering in Calamares**

I've been putting together a script that takes an **original CachyOS desktop ISO** and produces a modified ISO with bcachefs support integrated into Calamares, including multi-device storage and tiering options.

This is an unofficial, experimental project. Feedback, code review and testing on spare hardware or disposable VMs are welcome.


The source archive contains exactly the payload embedded in the script, including the build scripts, PKGBUILD, installer modifications and tiering UI.

The script extracts the original live filesystem, updates its packages, and builds a modified Calamares inside that extracted environment. Calamares uses baseline **x86-64 build flags**, rather than `-march=native` or an intentional x86-64-v3 requirement.

It also installs bcachefs-tools and bcachefs-dkms, builds matching modules for both `linux-cachyos` and `linux-cachyos-lts`, and regenerates their live initramfs images. Bcachefs is provided as a DKMS module included in the initramfs—not compiled directly into the kernel.

The installer adds controls for:

- Single-device or multi-device bcachefs root filesystems.
- SSD, HDD and normal device roles.
- Foreground, background, promotion and metadata targets.
- Zstd, LZ4 or no compression.
- Separate data and metadata replica counts.
- An SSD-to-HDD tiering preset.

Included fixes address Calamares’ Python `__file__` handling, integer overflow when passing large disk sizes through its Python/C++ interface, and the missing `bcachefs.ko.BTF.1` build failure. The BTF workaround disables module BTF generation specifically for bcachefs on the standard CachyOS kernel.

To use it, start with the original CachyOS desktop ISO and run:

```bash
bash patch-cachyos-bcachefs-iso.sh \
  cachyos-desktop-linux-260809.iso \
  cachyos-desktop-bcachefs.260809.iso
```

Substitute your actual filenames. Run it from a normal user account; it requests sudo when necessary and automatically installs missing host dependencies.

You need an x86-64 CachyOS build host—or an appropriately configured Arch installation with CachyOS repositories—an internet connection, and roughly **50–70 GiB of free space**. This performs actual compilation and package downloads, so expect it to take some time.

The original ISO remains unchanged. The output includes a new ISO, SHA-256 checksum, build log, package/version report and the compiled Calamares package. The script checks the generated root filesystem and boot files before publishing the output. It also adjusts the live installer launcher so it does not immediately replace the custom installer with the standard package.

There are several current limitations:

- **Experimental:** source tests and synthetic remaster tests have passed, but the current complete build/install workflow still needs end-to-end validation. Use disposable test disks first.
- Bcachefs root installation requires **UEFI, GPT and Limine**, with a separate FAT32 partition mounted at `/boot`. The installer recommends 4 GiB for that partition. Preserving the ISO’s BIOS boot entries does not mean BIOS installation of a bcachefs root is supported.
- This creates new filesystems. It is **not an in-place conversion or migration tool**; selected installation partitions are formatted.
- Encrypted bcachefs members and complex storage stacks are unsupported. It currently accepts one member per physical disk, up to 16 members, and 1–3 replicas subject to the available disks.
- Tiering is not redundancy. The default is one data copy; an SSD can hold the only current copy of data.
- The script expects a specific CachyOS desktop ISO layout and rejects signed SquashFS images. It does not provide a Secure Boot signing workflow.
- Calamares is pinned to a specific source revision, while system packages come from current repositories. Future upstream changes may require script updates, and builds are not guaranteed to be bit-for-bit reproducible.
- **An accidental Swedish lesson:** the new bcachefs/tiering page and its validation messages ended up in Swedish entirely by accident. Apparently my native language sneaked into the code. *Cough.* 😅 This is a localization oversight, not a requirement to install CachyOS in Swedish.

I prefer distributing the script and its readable source over hosting a complete modified ISO because the changes are easier to inspect, review and compare between releases. Users obtain the base image directly from CachyOS, and distributing a fix means sharing a small script rather than uploading another multi-gigabyte image.

That makes distribution and maintenance more practical, but it moves compilation, downloads and temporary storage requirements to the person building the image. It also does **not** eliminate the need to trust and review the script: it uses root privileges and installs build dependencies.

If you try it, please include the input ISO filename, build report, relevant error log and hardware or VM details when reporting problems.
