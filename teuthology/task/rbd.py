import contextlib
import logging
import os

from cStringIO import StringIO
from ..orchestra import run
from teuthology import misc as teuthology
from teuthology import contextutil

log = logging.getLogger(__name__)

def default_image_name(role):
    return 'testimage.{role}'.format(role=role)

@contextlib.contextmanager
def create_image(ctx, config):
    """
    Create an rbd image.

    For example::

        tasks:
        - ceph:
        - rbd.create_image:
            client.0:
                image_name: testimage
                image_size: 100
                image_format: 1
            client.1:

    Image size is expressed as a number of megabytes; default value
    is 10240.

    Image format value must be either 1 or 2; default value is 1.

    """
    assert isinstance(config, dict) or isinstance(config, list), \
        "task create_image only supports a list or dictionary for configuration"

    if isinstance(config, dict):
        images = config.items()
    else:
        images = [(role, None) for role in config]

    for role, properties in images:
        if properties is None:
            properties = {}
        name = properties.get('image_name', default_image_name(role))
        size = properties.get('image_size', 10240)
        fmt = properties.get('image_format', 1)
        (remote,) = ctx.cluster.only(role).remotes.keys()
        log.info('Creating image {name} with size {size}'.format(name=name,
                                                                 size=size))
        remote.run(
            args=[
                'LD_LIBRARY_PATH=/tmp/cephtest/binary/usr/local/lib',
                '/tmp/cephtest/enable-coredump',
                '/tmp/cephtest/binary/usr/local/bin/ceph-coverage',
                '/tmp/cephtest/archive/coverage',
                '/tmp/cephtest/binary/usr/local/bin/rbd',
                '-c', '/tmp/cephtest/ceph.conf',
                '-p', 'rbd',
                'create',
                '--format', str(fmt),
                '--size', str(size),
                name,
                ],
            )
    try:
        yield
    finally:
        log.info('Deleting rbd images...')
        for role, properties in images:
            if properties is None:
                properties = {}
            name = properties.get('image_name', default_image_name(role))
            (remote,) = ctx.cluster.only(role).remotes.keys()
            remote.run(
                args=[
                    'LD_LIBRARY_PATH=/tmp/cephtest/binary/usr/local/lib',
                    '/tmp/cephtest/enable-coredump',
                    '/tmp/cephtest/binary/usr/local/bin/ceph-coverage',
                    '/tmp/cephtest/archive/coverage',
                    '/tmp/cephtest/binary/usr/local/bin/rbd',
                    '-c', '/tmp/cephtest/ceph.conf',
                    '-p', 'rbd',
                    'rm',
                    name,
                    ],
                )

@contextlib.contextmanager
def modprobe(ctx, config):
    """
    Load the rbd kernel module..

    For example::

        tasks:
        - ceph:
        - rbd.create_image: [client.0]
        - rbd.modprobe: [client.0]
    """
    log.info('Loading rbd kernel module...')
    for role in config:
        (remote,) = ctx.cluster.only(role).remotes.keys()
        remote.run(
            args=[
                'sudo',
                'modprobe',
                'rbd',
                ],
            )
    try:
        yield
    finally:
        log.info('Unloading rbd kernel module...')
        for role in config:
            (remote,) = ctx.cluster.only(role).remotes.keys()
            remote.run(
                args=[
                    'sudo',
                    'modprobe',
                    '-r',
                    'rbd',
                    # force errors to be ignored; necessary if more
                    # than one device was created, which may mean
                    # the module isn't quite ready to go the first
                    # time through.
                    run.Raw('||'),
                    'true',
                    ],
                )

@contextlib.contextmanager
def dev_create(ctx, config):
    """
    Map block devices to rbd images.

    For example::

        tasks:
        - ceph:
        - rbd.create_image: [client.0]
        - rbd.modprobe: [client.0]
        - rbd.dev_create:
            client.0: testimage.client.0
    """
    assert isinstance(config, dict) or isinstance(config, list), \
        "task dev_create only supports a list or dictionary for configuration"

    if isinstance(config, dict):
        role_images = config.items()
    else:
        role_images = [(role, None) for role in config]

    log.info('Creating rbd block devices...')
    for role, image in role_images:
        if image is None:
            image = default_image_name(role)
        (remote,) = ctx.cluster.only(role).remotes.keys()

        # add udev rule for creating /dev/rbd/pool/image
        remote.run(
            args=[
                'echo',
                'KERNEL=="rbd[0-9]*", PROGRAM="/tmp/cephtest/binary/usr/local/bin/ceph-rbdnamer %n", SYMLINK+="rbd/%c{1}/%c{2}"',
                run.Raw('>'),
                '/tmp/cephtest/51-rbd.rules',
                ],
            )
        remote.run(
            args=[
                'sudo',
                'mv',
                '/tmp/cephtest/51-rbd.rules',
                '/etc/udev/rules.d/',
                ],
            )

        secretfile = '/tmp/cephtest/data/{role}.secret'.format(role=role)
        teuthology.write_secret_file(remote, role, secretfile)

        remote.run(
            args=[
                'sudo',
                'LD_LIBRARY_PATH=/tmp/cephtest/binary/usr/local/lib',
                '/tmp/cephtest/enable-coredump',
                '/tmp/cephtest/binary/usr/local/bin/ceph-coverage',
                '/tmp/cephtest/archive/coverage',
                '/tmp/cephtest/binary/usr/local/bin/rbd',
                '-c', '/tmp/cephtest/ceph.conf',
                '--user', role.rsplit('.')[-1],
                '--secret', secretfile,
                '-p', 'rbd',
                'map',
                image,
                run.Raw('&&'),
                # wait for the symlink to be created by udev
                'while', 'test', '!', '-e', '/dev/rbd/rbd/{image}'.format(image=image), run.Raw(';'), 'do',
                'sleep', '1', run.Raw(';'),
                'done',
                ],
            )
    try:
        yield
    finally:
        log.info('Unmapping rbd devices...')
        for role, image in role_images:
            if image is None:
                image = default_image_name(role)
            (remote,) = ctx.cluster.only(role).remotes.keys()
            remote.run(
                args=[
                    'sudo',
                    'LD_LIBRARY_PATH=/tmp/cephtest/binary/usr/local/lib',
                    '/tmp/cephtest/enable-coredump',
                    '/tmp/cephtest/binary/usr/local/bin/ceph-coverage',
                    '/tmp/cephtest/archive/coverage',
                    '/tmp/cephtest/binary/usr/local/bin/rbd',
                    '-c', '/tmp/cephtest/ceph.conf',
                    '-p', 'rbd',
                    'unmap',
                    '/dev/rbd/rbd/{imgname}'.format(imgname=image),
                    run.Raw('&&'),
                    # wait for the symlink to be deleted by udev
                    'while', 'test', '-e', '/dev/rbd/rbd/{image}'.format(image=image),
                    run.Raw(';'),
                    'do',
                    'sleep', '1', run.Raw(';'),
                    'done',
                    ],
                )
            remote.run(
                args=[
                    'sudo',
                    'rm',
                    '-f',
                    '/etc/udev/rules.d/51-rbd.rules',
                    ],
                wait=False,
                )

@contextlib.contextmanager
def mkfs(ctx, config):
    """
    Create a filesystem on a block device.

    For example::

        tasks:
        - ceph:
        - rbd.create_image: [client.0]
        - rbd.modprobe: [client.0]
        - rbd.dev_create: [client.0]
        - rbd.mkfs:
            client.0:
                fs_type: xfs
    """
    assert isinstance(config, list) or isinstance(config, dict), \
        "task mkfs must be configured with a list or dictionary"
    if isinstance(config, dict):
        images = config.items()
    else:
        images = [(role, None) for role in config]

    for role, properties in images:
        if properties is None:
            properties = {}
        (remote,) = ctx.cluster.only(role).remotes.keys()
        image = properties.get('image_name', default_image_name(role))
        fs = properties.get('fs_type', 'ext3')
        remote.run(
            args=[
                'sudo',
                'mkfs',
                '-t', fs,
                '/dev/rbd/rbd/{image}'.format(image=image),
                ],
            )
    yield

@contextlib.contextmanager
def mount(ctx, config):
    """
    Mount an rbd image.

    For example::

        tasks:
        - ceph:
        - rbd.create_image: [client.0]
        - rbd.modprobe: [client.0]
        - rbd.mkfs: [client.0]
        - rbd.mount:
            client.0: testimage.client.0
    """
    assert isinstance(config, list) or isinstance(config, dict), \
        "task mount must be configured with a list or dictionary"
    if isinstance(config, dict):
        role_images = config.items()
    else:
        role_images = [(role, None) for role in config]

    def strip_client_prefix(role):
        PREFIX = 'client.'
        assert role.startswith(PREFIX)
        id_ = role[len(PREFIX):]
        return id_

    mnt_template = '/tmp/cephtest/mnt.{id}'
    for role, image in role_images:
        if image is None:
            image = default_image_name(role)
        (remote,) = ctx.cluster.only(role).remotes.keys()
        id_ = strip_client_prefix(role)
        mnt = mnt_template.format(id=id_)
        remote.run(
            args=[
                'mkdir',
                '--',
                mnt,
                ]
            )

        remote.run(
            args=[
                'sudo',
                'mount',
                '/dev/rbd/rbd/{image}'.format(image=image),
                mnt,
                ],
            )

    try:
        yield
    finally:
        log.info("Unmounting rbd images...")
        for role, image in role_images:
            if image is None:
                image = default_image_name(role)
            (remote,) = ctx.cluster.only(role).remotes.keys()
            id_ = strip_client_prefix(role)
            mnt = mnt_template.format(id=id_)
            remote.run(
                args=[
                    'sudo',
                    'umount',
                    mnt,
                    ],
                )

            remote.run(
                args=[
                    'rmdir',
                    '--',
                    mnt,
                    ]
                )

# Determine the canonical path for a given path on the host
# representing the given role.  A canonical path contains no
# . or .. components, and includes no symbolic links.
def canonical_path(ctx, role, path):
    version_fp = StringIO()
    ctx.cluster.only(role).run(
        args=[ 'readlink', '-f', path ],
        stdout=version_fp,
        )
    canonical_path = version_fp.getvalue().rstrip('\n')
    version_fp.close()
    return canonical_path

@contextlib.contextmanager
def run_xfstests(ctx, config):
    """
    Run xfstests over specified devices.

    Warning: both the test and scratch devices specified will be
    overwritten.  Normally xfstests modifies (but does not destroy)
    the test device, but for now the run script used here re-makes
    both filesystems.

    Note: Only one instance of xfstests can run on a single host at
    a time, although this is not enforced.

    This task in its current form needs some improvement.  For
    example, it assumes all roles provided in the config are
    clients, and that the config provided is a list of key/value
    pairs.  For now please use the xfstests() interface, below.

    For example::

        tasks:
        - ceph:
        - rbd.run_xfstests:
            client.0:
                test_dev: 'test_dev'
                scratch_dev: 'scratch_dev'
                fs_type: 'xfs'
                tests: '1-9 11-15 17 19-21 26-28 31-34 41 45-48'
    """

    for role, properties in config.items():
        test_dev = properties.get('test_dev')
        assert test_dev is not None, \
            "task run_xfstests requires test_dev to be defined"
        test_dev = canonical_path(ctx, role, test_dev)

        scratch_dev = properties.get('scratch_dev')
        assert scratch_dev is not None, \
            "task run_xfstests requires scratch_dev to be defined"
        scratch_dev = canonical_path(ctx, role, scratch_dev)

        fs_type = properties.get('fs_type')
        tests = properties.get('tests')

        (remote,) = ctx.cluster.only(role).remotes.keys()

        # Fetch the test script
        test_root = '/tmp/cephtest'
        test_script = 'run_xfstests.sh'
        test_path = os.path.join(test_root, test_script)

        git_branch = 'master'
        test_url = 'https://raw.github.com/ceph/ceph/{branch}/qa/{script}'.format(branch=git_branch, script=test_script)
        # test_url = 'http://ceph.newdream.net/git/?p=ceph.git;a=blob_plain;hb=refs/heads/{branch};f=qa/{script}'.format(branch=git_branch, script=test_script)

        log.info('Fetching {script} for {role} from {url}'.format(script=test_script,
                                                                role=role,
                                                                url=test_url))
        args = [ 'wget', '-O', test_path, '--', test_url ]
        remote.run(args=args)

        log.info('Running xfstests on {role}:'.format(role=role))
        log.info('       test device: {dev}'.format(dev=test_dev))
        log.info('    scratch device: {dev}'.format(dev=scratch_dev))
        log.info('     using fs_type: {fs_type}'.format(fs_type=fs_type))
        log.info('      tests to run: {tests}'.format(tests=tests))

        # Note that the device paths are interpreted using
        # readlink -f <path> in order to get their canonical
        # pathname (so it matches what the kernel remembers).
        args = [
            'LD_LIBRARY_PATH=/tmp/cephtest/binary/usr/local/lib',
            '/tmp/cephtest/enable-coredump',
            '/tmp/cephtest/binary/usr/local/bin/ceph-coverage',
            '/tmp/cephtest/archive/coverage',
            '/usr/bin/sudo',
            '/bin/bash',
            test_path,
            '-f', fs_type,
            '-t', test_dev,
            '-s', scratch_dev,
            ]
        if tests:
            args.append(tests)

        remote.run(args=args)
    try:
        yield
    finally:
        for role, properties in config.items():
            (remote,) = ctx.cluster.only(role).remotes.keys()
            log.info('Removing {script} on {role}'.format(script=test_script,
                                                        role=role))
            args = [ 'rm', '-f', test_path ]
            remote.run(args=args)

@contextlib.contextmanager
def xfstests(ctx, config):
    """
    Run xfstests over rbd devices.  This interface sets up all
    required configuration automatically if not otherwise specified.
    Note that only one instance of xfstests can run on a single host
    at a time.

    For example::

        tasks:
        - ceph:
        # Image sizes are in MB
        - rbd.xfstests:
            client.0:
                test_image: 'test_image'
                test_size: 250
                scratch_image: 'scratch_image'
                scratch_size: 250
                fs_type: 'xfs'
                tests: '1-9 11-15 17 19-21 26-28 31-34 41 45-48'
    """
    if config is None:
        config = { 'all': None }
    assert isinstance(config, dict) or isinstance(config, list), \
        "task xfstests only supports a list or dictionary for configuration"
    if isinstance(config, dict):
        config = teuthology.replace_all_with_clients(ctx.cluster, config)
        runs = config.items()
    else:
        runs = [(role, None) for role in config]

    running_xfstests = {}
    for role, properties in runs:
        assert role.startswith('client.'), \
            "task xfstests can only run on client nodes"
        for host, roles_for_host in ctx.cluster.remotes.items():
            if role in roles_for_host:
                assert host not in running_xfstests, \
                    "task xfstests allows only one instance at a time per host"
                running_xfstests[host] = True

    for role, properties in runs:
        if properties is None:
            properties = {}

        test_image = properties.get('test_image', 'test_image')
        test_size = properties.get('test_size', 1000)
        scratch_image = properties.get('scratch_image', 'scratch_image')
        scratch_size = properties.get('scratch_size', 1000)

        test_image_config = {}
        test_image_config['image_name'] = test_image
        test_image_config['image_size'] = test_size

        scratch_image_config = {}
        scratch_image_config['image_name'] = scratch_image
        scratch_image_config['image_size'] = scratch_size


        test_config = {}
        test_config['test_dev'] = \
                '/dev/rbd/rbd/{image}'.format(image=test_image)
        test_config['scratch_dev'] = \
                '/dev/rbd/rbd/{image}'.format(image=scratch_image)
        test_config['fs_type'] = properties.get('fs_type', 'xfs')
        test_config['tests'] = properties.get('tests', None)

        log.info('Setting up xfstests using RBD images:')
        log.info('      test ({size} MB): {image}'.format(size=test_size,
                                                        image=test_image))
        log.info('   scratch ({size} MB): {image}'.format(size=scratch_size,
                                                        image=scratch_image))
        with contextutil.nested(
            lambda: create_image(ctx=ctx, \
                        config={ role: test_image_config }),
            lambda: create_image(ctx=ctx, \
                        config={ role: scratch_image_config }),
            lambda: modprobe(ctx=ctx, config={ role: None }),
            lambda: dev_create(ctx=ctx, config={ role: test_image }),
            lambda: dev_create(ctx=ctx, config={ role: scratch_image }),
            lambda: run_xfstests(ctx=ctx, config={ role: test_config }),
            ):
            yield


@contextlib.contextmanager
def task(ctx, config):
    """
    Create and mount an rbd image.

    For example, you can specify which clients to run on::

        tasks:
        - ceph:
        - rbd: [client.0, client.1]

    There are a few image options::

        tasks:
        - ceph:
        - rbd:
            client.0: # uses defaults
            client.1:
                image_name: foo
                image_size: 2048
                image_format: 2
                fs_type: xfs

    To use default options on all clients::

        tasks:
        - ceph:
        - rbd:
            all:

    To create 20GiB images and format them with xfs on all clients::

        tasks:
        - ceph:
        - rbd:
            all:
              image_size: 20480
              fs_type: xfs
    """
    if config is None:
        config = { 'all': None }
    norm_config = config
    if isinstance(config, dict):
        norm_config = teuthology.replace_all_with_clients(ctx.cluster, config)
    if isinstance(norm_config, dict):
        role_images = {}
        for role, properties in norm_config.iteritems():
            if properties is None:
                properties = {}
            role_images[role] = properties.get('image_name')
    else:
        role_images = norm_config

    log.debug('rbd config is: %s', norm_config)

    with contextutil.nested(
        lambda: create_image(ctx=ctx, config=norm_config),
        lambda: modprobe(ctx=ctx, config=norm_config),
        lambda: dev_create(ctx=ctx, config=role_images),
        lambda: mkfs(ctx=ctx, config=norm_config),
        lambda: mount(ctx=ctx, config=role_images),
        ):
        yield
