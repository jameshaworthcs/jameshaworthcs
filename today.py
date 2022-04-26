import datetime
from dateutil import relativedelta
import requests
import os
from xml.dom import minidom
import multiprocessing
import time
import random

USER_NAME = os.environ['USER_NAME'] # 'Andrew6rant'
TOKENS = os.environ['TOKENS'].split(',') # Personal access tokens with permissions: read:enterprise, read:org, read:repo_hook, read:user, repo


def daily_readme():
    """
    Returns the length of time since I was born
    e.g. 'XX years, XX months, XX days'
    """
    birth = datetime.datetime(2002, 7, 5)
    diff = relativedelta.relativedelta(datetime.datetime.today(), birth)
    return '{} {}, {} {}, {} {}'.format(
        diff.years, 'year' + format_plural(diff.years), 
        diff.months, 'month' + format_plural(diff.months), 
        diff.days, 'day' + format_plural(diff.days))


def format_plural(unit):
    """
    Returns a properly formatted number
    e.g.
    'day' + format_plural(diff.days) == 5
    >>> '5 days'
    'day' + format_plural(diff.days) == 1
    >>> '1 day'
    """
    if unit != 1: return 's'
    return ''


def graph_commits(start_date, end_date):
    """
    Uses GitHub's GraphQL v4 API to return my total commit count
    """
    print(graph_commits.__name__) # debug for each time GraphQL API is called
    query = '''
    query($start_date: DateTime!, $end_date: DateTime!, $login: String!) {
        user(login: $login) {
            contributionsCollection(from: $start_date, to: $end_date) {
                contributionCalendar {
                    totalContributions
                }
            }
        }
    }'''
    variables = {'start_date': start_date,'end_date': end_date, 'login': USER_NAME}
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables':variables}, headers={'authorization': 'token '+ random.choice(TOKENS)})
    if request.status_code == 200:
        return int(request.json()['data']['user']['contributionsCollection']['contributionCalendar']['totalContributions'])
    raise Exception('The request has failed, graph_commits()')


def graph_repos_stars_loc(count_type, owner_affiliation, cursor=None, add_loc=0, del_loc=0):
    """
    Uses GitHub's GraphQL v4 API to return my total repository, star, or lines of code count.
    """
    print(graph_repos_stars_loc.__name__, count_type) # debug for each time GraphQL API is called
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            stargazers {
                                totalCount
                            }
                            owner {
                                id
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables':variables}, headers={'authorization': 'token '+ random.choice(TOKENS)})
    if request.status_code == 200:
        if count_type == 'repos':
            return request.json()['data']['user']['repositories']['totalCount']
        elif count_type == 'stars':
            return stars_counter(request.json()['data']['user']['repositories']['edges'])
        else:
            return all_repo_names_multiprocessing(request.json()['data']['user']['repositories'], add_loc, del_loc)
    raise Exception('The request has failed, graph_repos_stars()')


def all_repo_names_multiprocessing(repositories, add_loc=0, del_loc=0):
    """
    Returns the total number of lines of code written by my account
    """
    pool = multiprocessing.Pool(multiprocessing.cpu_count()) # 12 on my machine
    pool_list = []
    for index in range(len(repositories['edges'])):
        name_with_owner = repositories['edges'][index]['node']['nameWithOwner'].split('/')
        owner, repo_name = name_with_owner
        loc = pool.apply_async(query_loc, args=[owner, repo_name])
        pool_list.append(loc)
        time.sleep(0.4) # prevent rate limit
    for index in range(len(repositories['edges'])):
        both_loc = pool_list[index].get()
        add_loc += both_loc[0]
        del_loc += both_loc[1]
        time.sleep(0.25) # prevent rate limit

    if repositories['edges'] == [] or not repositories['pageInfo']['hasNextPage']: # end of repo list
        total_loc = add_loc - del_loc
        return [add_loc, del_loc, total_loc]
    else: return graph_repos_stars_loc('LOC', ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'], repositories['pageInfo']['hasNextPage'], add_loc, del_loc)


def query_loc(owner, repo_name, addition_total=0, deletion_total=0, cursor=None):
    """
    Uses GitHub's GraphQL v4 API to fetch 100 commits at a time
    This is a separate function from graph_commits and graph_repos_stars_loc, because this is called dozens of times
    """
    print(query_loc.__name__) # debug for each time GraphQL API is called
    query = '''
    query ($repo_name: String!, $owner: String!, $cursor: String) {
        repository(name: $repo_name, owner: $owner) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor) {
                            totalCount
                            edges {
                                node {
                                    ... on Commit {
                                        committedDate
                                    }
                                    author {
                                        user {
                                            id
                                        }
                                    }
                                    deletions
                                    additions
                                }
                            }
                            pageInfo {
                                endCursor
                                hasNextPage
                            }
                        }
                    }
                }
            }
        }
    }'''
    variables = {'repo_name': repo_name, 'owner': owner, 'cursor': cursor}
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables':variables}, headers={'authorization': 'token '+ random.choice(TOKENS)})
    if request.status_code == 200:
        if request.json()['data']['repository']['defaultBranchRef'] != None: # Only count commits if repo isn't empty
            return loc_counter_one_repo(owner, repo_name, request.json()['data']['repository']['defaultBranchRef']['target']['history'], addition_total, deletion_total)
        else: return 0
    elif request.status_code == 403:
        raise Exception('Too many requests in a short amount of time!\nYou\'ve hit the non-documented anti-abuse limit!')
    print(request.status_code)
    raise Exception('The request has failed, query_loc()')


def loc_counter_one_repo(owner, repo_name, history, addition_total, deletion_total):
    """
    Recursively call query_loc (since GraphQL can only search 100 commits at a time) 
    only adds the LOC value of commits authored by me
    """
    for node in history['edges']:
        if node['node']['author']['user'] == {'id': 'MDQ6VXNlcjU3MzMxMTM0'}:
            addition_total += node['node']['additions']
            deletion_total += node['node']['deletions']

    if history['edges'] == [] or not history['pageInfo']['hasNextPage']:
        return addition_total, deletion_total
    else: return query_loc(owner, repo_name, addition_total, deletion_total, history['pageInfo']['endCursor'])


def stars_counter(data):
    """
    Count total stars in repositories owned by me
    """
    total_stars = 0
    for node in data: total_stars += node['node']['stargazers']['totalCount']
    return total_stars


def svg_overwrite(filename, age_data, commit_data, star_data, repo_data, contrib_data, loc_data):
    """
    Parse SVG files and update elements with my age, commits, stars, repositories, and lines written
    """
    svg = minidom.parse(filename)
    f = open(filename, mode='w', encoding='utf-8')
    tspan = svg.getElementsByTagName('tspan')
    tspan[30].firstChild.data = age_data
    tspan[65].firstChild.data = repo_data
    tspan[67].firstChild.data = contrib_data
    tspan[69].firstChild.data = commit_data
    tspan[71].firstChild.data = star_data
    tspan[73].firstChild.data = loc_data[2]
    tspan[74].firstChild.data = loc_data[0] + '++'
    tspan[75].firstChild.data = loc_data[1] + '--'
    f.write(svg.toxml('utf-8').decode('utf-8'))
    f.close()


def commit_counter(date):
    """
    Counts up my total commits.
    Loops commits per year (starting backwards from today, continuing until my account's creation date)
    """
    total_commits = 0
    # since GraphQL's contributionsCollection has a maximum reach of one year
    while date.isoformat() > '2019-11-02T00:00:00.000Z': # one day before my very first commit
        old_date = date.isoformat()
        date = date - datetime.timedelta(days=365)
        total_commits += graph_commits(date.isoformat(), old_date)
    return total_commits


def svg_element_getter(filename):
    """
    Prints the element index of every element in the SVG file
    """
    svg = minidom.parse(filename)
    open(filename, mode='r', encoding='utf-8')
    tspan = svg.getElementsByTagName('tspan')
    for index in range(len(tspan)): print(index, tspan[index].firstChild.data)


def user_id_getter(username):
    """
    Returns the account ID of the username
    """
    print(user_id_getter.__name__) # debug for each time GraphQL API is called
    query = '''
    query($login: String!){
        user(login: $login) {
            id
        }
    }'''
    variables = {'login': username}
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables':variables}, headers={'authorization': 'token '+ random.choice(TOKENS)})
    if request.status_code == 200:
        return {'id': request.json()['data']['user']['id']}
    raise Exception('The request has failed, user_id_getter()')


def main():
    """
    Runs program over each SVG image
    """
    # OWNER_ID temporarily hardcoded as multiprocessing cannot access global variable
    #   define global variable for owner ID
    #   e.g {'id': 'MDQ6VXNlcjU3MzMxMTM0'} for username 'Andrew6rant'
    #   OWNER_ID = user_id_getter(USER_NAME)

    start_age = time.perf_counter()
    age_data = daily_readme()
    difference_age = time.perf_counter() - start_age
    print('Age fetching time:', difference_age)

    start_loc = time.perf_counter()
    total_loc = graph_repos_stars_loc('LOC', ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'])
    difference_loc = time.perf_counter() - start_loc
    print('Loc fetching time:', difference_loc)

    # f' for whitespace, "{;,}" for commas
    start_commit = time.perf_counter()
    commit_data = f'{"{:,}".format(commit_counter(datetime.datetime.today())): <7}'
    difference_commit = time.perf_counter() - start_commit
    print('Commit fetching time:', difference_commit)

    start_star = time.perf_counter()
    star_data = "{:,}".format(graph_repos_stars_loc('stars', ['OWNER']))
    difference_star = time.perf_counter() - start_star
    print('Star fetching time:', difference_star)

    start_repo = time.perf_counter()
    repo_data = f'{"{:,}".format(graph_repos_stars_loc("repos", ["OWNER"])): <2}'
    difference_repo = time.perf_counter() - start_repo
    print('Repo fetching time:', difference_repo)

    start_contrib = time.perf_counter()
    contrib_data = f'{"{:,}".format(graph_repos_stars_loc("repos", ["OWNER", "COLLABORATOR", "ORGANIZATION_MEMBER"])): <2}'
    difference_contrib = time.perf_counter() - start_contrib
    print('Contrib fetching time:', difference_contrib)

    for index in range(len(total_loc)): total_loc[index] = "{:,}".format(total_loc[index]) # format added, deleted, and total LOC

    svg_overwrite('dark_mode.svg', age_data, commit_data, star_data, repo_data, contrib_data, total_loc)
    svg_overwrite('light_mode.svg', age_data, commit_data, star_data, repo_data, contrib_data, total_loc)

if __name__ == '__main__':
    main()
