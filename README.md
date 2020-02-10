# Barnum

Easily manage multiple Circus instances across multiple hosts.

## Configuration

### Structure

Barnum currently relies on a very specific directory structure in order to discover Circus hosts:

```
tree /users/user1/circus
├── host1
│   ├── circus.ini
│   ├── circus.log
│   ├── watcher1.stderr.log
│   └── watcher1.stdout.log
└── init_circus -> /users/$USER/circus/init_circus
```

In other words, it expects that every user that is running a Circus instance will have a `~/circus` directory, and that this each child directory thereof will contain a `circus.ini` folder. Each `circus.ini` file it discovered will be parsed to determine its endpoint, and the host will be derived from the name its parent. Then, `ssh` commands will be built from this information, and used to delegate commands across multiple users/hosts simultaneously (threaded).

### Config File

You will also need a config file, so that `barnum` knows what to search for.

Create `circus_users.yaml` in the same directory as `barnum.py`. Its contents should look something like this:

```yaml
---

- user1
- user2
```

## Operations

### `barnum`

Perform Circus operations across multiple hosts

#### System Overview

```
$ python barnum.py --verbose
barnum: Circus is configured on the following hosts: host1, host2
barnum: Processing host1
barnum: Processing host2
barnum: bailey cmd: ssh -q host1 bailey --verbose
barnum: bailey cmd: ssh -q host2 bailey --verbose

bailey: Derived circus user user1 from /etc/systemd/system/circus_user1_host2.service
bailey: circus cmd: circusctl --endpoint tcp://host2:8385 status
bailey: Derived circus user user2 from /etc/systemd/system/circus_user2_host1.service
bailey: circus cmd: circusctl --endpoint tcp://host1:5775 status
bailey: Derived circus user user3 from /etc/systemd/system/circus_user3_host2.service
bailey: circus cmd: circusctl --endpoint tcp://host2:5555 status
--- HOST1 ---
--------------------------------------------------------------------------------
circus_user2_host1.service  enabled active (running)
  watcher1: active
================================================================================

--- HOST2 ---
--------------------------------------------------------------------------------
circus_user1_host2.service       enabled active (running)
  watcher2: active
--------------------------------------------------------------------------------
circus_user3_host2.service     enabled active (running)
  watcher3: active
--------------------------------------------------------------------------------
circus_user4_host2.service      disabled        inactive (dead)
  No circus expected
================================================================================
```

What's happening here:

1. For each user in `circus_users.yaml`, derive the hosts it has `circus` instances on from the directory structure described above
1. For every host:
    1. SSH to `host_n`
    1. Use `sysctl list-unit-files` to determine all `circus` unit files
    1. Examine each unit file to determine the user it is for
    1. Call `bailey` for each user on `host_n`

#### Manage All Circus Instances on Given Host

Get status of all Circus instances run on `host1`:

```
$ python barnum.py host1 --verbose
Circus is configured on the following hosts: host1
Processing user1@host1
bailey cmd: ssh -q host1 /path/to/bailey user1 --verbose
circus cmd: circusctl --endpoint tcp://host1:5775 status
---
watcher1: active
```

What's happening here:

1. SSH to `host1`
1. Use `sysctl list-unit-files` to determine all `circus` unit files
1. Examine each unit file to determine the user it is for
1. For each user:
    1. Construct path to `circus` config file based on given user and host
    1. Parse config file to determine `circus` endpoint
    1. Call `bailey` for derived endpoint

#### Manage Specific Circus Instance on Given Host

Get status of Circus instance run by `user1@host1`.

```
$ python barnum.py user1@host1 --verbose
Circus is configured on the following hosts: host1
Processing user1@host1
bailey cmd: ssh -q host1 bailey user1 --verbose
circus cmd: circusctl --endpoint tcp://host1:5775 status
---
watcher1: active
```

What's happening here:

1. SSH to `host1`
1. Construct path to `circus` config file based on given user and host
1. Parse config file to determine `circus` endpoint
1. Call `bailey` for derived endpoint

#### Sending Commands to Bailey/Circus

There are two advanced use cases here:

1. Send commands to `bailey`
2. Send commands to `circus`
3. Send commands to both `bailey` and `circus`

It is useful to experiment with these using `--dry-run`, in order to prevent things from getting broken. For example,

```
$ python barnum.py user1@host1 --verbose --dry-run
barnum: Processing user1@host1
---
DRY RUN; would execute: ssh -q host1 bailey user1 --verbose
```

##### Send commands to `bailey`

This would print the help message for every `bailey` instance:

```$ python barnum.py -- --help```

To send specific `circus` commands to each `bailey` instance, you'll need to use something like the following:

```$ python barnum.py user1@host1 --verbose -- -- stats```

Everything following the `--` will be sent directly to `bailey`, without any changes. `bailey` will then send everything after the _second_ `--` directly to `circus` (more on that below).


### `bailey`

Perform Circus operations on a single hosts (but possibly multiple users)

#### Manage All Circus Instances on Current Host

Get status of all Circus instances run on current host (`host1`):

```
$ bailey --verbose
--- host1 ---
--------------------------------------------------------------------------------
circus_user1_host1.service      disabled        inactive (dead)
  No circus expected
--------------------------------------------------------------------------------
bailey: Derived circus user user1 from /etc/systemd/system/circus_user1_host1.service
bailey: circus cmd: circusctl --endpoint tcp://host1:5755 status
circus_user1_host1.service  enabled active (running)
  watcher1: active
================================================================================
```

What's happening here:

1. Use `sysctl list-unit-files` to determine all `circus` unit files
1. Examine each unit file to determine the user it is for
1. For each user:
    1. Construct path to `circus` config file based on given user and host
    1. Parse config file to determine `circus` endpoint
    1. Call `bailey` for derived endpoint

#### Manage Specific Circus Instance on Current Host

Get status of Circus instance run by `user1@host1` (given user at current host).

```
$ bailey user1 --verbose
bailey: circus cmd: circusctl --endpoint tcp://host1:5555 status
watcher1: active
```

What's happening here:

1. Construct path to `circus` config file based on given user and host
1. Parse config file to determine `circus` endpoint
1. Call `bailey` for derived endpoint


##### Send commands to `circus`

To send specific `circus` commands, you'll need to use something like the following:

```$ python barnum.py user1@host1 --verbose -- stats```

Everything following the `--` will be sent directly to `circus`, without any changes
