import os
import sys
import time
import logging
import requests
import subprocess
from json import dumps

# https://urllib3.readthedocs.org/en/latest/security.html#insecureplatformwarning
logging.captureWarnings(True)

headers = {"Authorization": "token "+os.getenv("GITHUB_TOKEN")}
circleurl = "https://circleci.com/gh/codecov/testsuite/"+os.getenv("CIRCLE_BUILD_NUM")


def curl(method, *args, **kwargs):
    "wrapper to only print on errors"
    res = getattr(requests, method)(*args, **kwargs)
    try:
        res.raise_for_status()
    except:
        print(res.text)
        raise
    return res


def bash(cmd):
    return subprocess.check_output(cmd, shell=True).decode('utf-8')


def set_state(slug, commit, state):
    # set head of wip to pending
    return curl('post', "https://api.github.com/repos/%s/statuses/%s" % (slug, commit),
                headers=headers,
                data=dumps(dict(state=state,
                                target_url=circleurl,
                                context="codecov/examples")))


def get_head(slug, branch):
    print(slug + " \033[92mGet head\033[0m")
    res = curl('get', "https://api.github.com/repos/%s/git/refs/heads/%s" % (slug, branch), headers=headers)
    return res.json()['object']['sha']


def get_tree(slug, commit):
    print(slug + " \033[92mGet tree\033[0m")
    res = curl('get', "https://api.github.com/repos/%s/git/commits/%s" % (slug, commit), headers=headers)
    return res.json()['tree']['sha']


def update_reference(slug, ref, commit):
    print(slug + " \033[92mPatch reference\033[0m")
    curl('patch', "https://api.github.com/repos/%s/git/refs/heads/%s" % (slug, ref), headers=headers,
         data=dumps(dict(sha=commit)))
    return True


try:
    repos = ['codecov/example-java', 'codecov/example-scala', 'codecov/example-xcode', 'codecov/example-c',
             'codecov/example-lua', 'codecov/example-go', 'codecov/example-python', 'codecov/example-php',
             'codecov/example-d', 'codecov/example-fortran', 'codecov/example-swift']
    total = len(repos)

    lang = os.getenv('TEST_LANG', 'bash')
    slug = os.getenv('TEST_SLUG', 'codecov/codecov-'+lang)
    sha = os.getenv('TEST_SHA', 'master')
    cmd = os.getenv('TEST_CMD', None)
    if not cmd:
        if lang == 'python':
            repos.remove('codecov/example-swift')  # bash only atm
            cmd = 'pip install --user git+https://github.com/%s.git@%s && codecov' % (slug, sha)
        elif lang == 'bash':
            repos.remove('codecov/example-c')  # python only
            cmd = 'bash <(curl -s https://raw.githubusercontent.com/%s/%s/codecov)' % (slug, sha)

    # set pending status
    set_state(slug, sha, "pending")

    # Make empty commit
    commits = {}
    for _slug in repos:
        # https://developer.github.com/v3/git/commits/#create-a-commit
        head = get_head(_slug, 'future')
        tree = get_tree(_slug, head)
        print(_slug + " \033[92mPost commit\033[0m")
        args = (os.getenv('CIRCLE_BUILD_NUM'), circleurl, cmd.replace(' --user', '') if 'python' in _slug else cmd)
        res = curl('post', "https://api.github.com/repos/%s/git/commits" % _slug,
                   headers=headers,
                   data=dumps(dict(message="Circle build #%s\n%s\n%s" % args,
                                   tree=tree,
                                   parents=[head],
                                   author=dict(name="Codecov Test Bot", email="hello@codecov.io"))))
        _sha = res.json()['sha']
        print("    Sha: " + _sha)
        update_reference(_slug, 'future', _sha)
        commits[_slug] = _sha

    # wait for travis to pick up builds
    print("Waiting 4 minutes...")
    time.sleep(240)

    # Wait for CI Status
    passed = 0
    while len(commits) > 0:
        print("Waiting 1 minutes...")
        time.sleep(60)
        # collect build numbers
        for _slug, commit in commits.items():
            print(_slug + " Checking Travis %s at %s..." % (_slug, commit))
            res = curl('get', "https://api.github.com/repos/%s/commits/%s/status" % (_slug, commit), headers=headers).json()
            state = res['state']
            print(_slug + ' State: ' + state + ' ' + res['statuses'][0]['target_url'])
            assert state in ('success', 'pending')
            if state == 'success':
                print(_slug + " Checking %s at %s..." % (_slug, commit))
                future = curl('get', "https://codecov.io/api/gh/%s?ref=%s" % (_slug, commit))
                if future.status_code == 404:
                    assert commit in future.json()['queue'], "%s at %.7s is not in Codecov upload queue" % (_slug, commit)
                    continue

                master = curl('get', "https://codecov.io/api/gh/%s?branch=master" % _slug)

                assert master.json()['report'] == future.json()['report'], "%s at %.7s reports do not match" % (_slug, commit)

                del commits[_slug]
                passed = passed + 1

    # submit states
    status = 'success' if len(commits) == 0 else 'failure'

    # set state status for heads
    set_state(slug, sha, status)

    sys.exit(status == 'failure')

except Exception:
    # set state status for heads
    set_state(slug, sha, 'error')
    raise
