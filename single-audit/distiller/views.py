import os
from datetime import date, timedelta
import time  # For use with Selenium. @todo: Replace with explicit waits
from io import StringIO

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render

import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support.ui import WebDriverWait

from .forms import AgencySelectionForm, __get_agency_name_from_prefix, __is_valid_agency_prefix

# @todo: Update this to actually download the file from the FAC. We'll get there.
#        https://harvester.census.gov/facdissem/PublicDataDownloads.aspx
#        Do it on some cached ongoing basis so you're not making people wait for
#        a 500-MB download.
DIRECTORY_NAME = 'single_audit_data_dump'
FILES_DIRECTORY = os.path.join(settings.BASE_DIR, 'distiller', DIRECTORY_NAME)

CHROME_DRIVER_LOCATION = os.path.join(settings.BASE_DIR, 'distiller/chromedriver')
FAC_URL = 'https://harvester.census.gov/facdissem/SearchA133.aspx'

DEPT_OF_TRANSPORTATION_PREFIX = '20'
FTA_SUBAGENCY_CODE = '5'

# @todo: Improve the naming here and make it more possible for it to be
#        extendable to different kinds of URLs.
FAC_URL = 'https://harvester.census.gov/facdissem/SearchA133.aspx'


def _calculate_start_date(time_difference=90, end_date=date.today()):
    """
    Calculate the date that's a certain number of days earlier than a given end
    date.

    Args:
        time_difference (int): non-negative integer representing how many days
                               earlier to calculate.

    Returns:
        A (date) string formatted appropriately for the Federal Audit Clearinghouse.
    """

    start_date = end_date - timedelta(time_difference)
    return _format_date_for_fac_fields(start_date)


def _format_date_for_fac_fields(date):
    """
    Format a date into a string that's consistent with the Federal Audit
    Clearinghouse's "from" and "to" date input requirements.

    Args:
        date (date): date object.

    Returns:
        A (date) string formatted for the Federal Audit Clearinghouse (MM/DD/YYYY).
    """

    return date.strftime("%m/%d/%Y")


def check_for_chromedriver():
    """
    Try to open Chromedriver at the specified location. If you can't, throw an
    exception.
    """

    try:
        chromedriver = open(CHROME_DRIVER_LOCATION)
        chromedriver.close()
    except IOError:
        # @todo: Make this error message more informative without potentially
        #        exposing sensitive system information.
        #
        #        Download link to provide:
        #        https://sites.google.com/a/chromium.org/chromedriver/downloads
        print("Chromedriver could not be opened.")


def list_completed_chrome_downloads(driver):
    """
    List the Chrome downloads that have completed.

    Credit where credit's due: https://stackoverflow.com/a/48267887/902981

    Args:
        driver (webdriver): a Selenium webdriver.

    Returns:
        A list of paths of downloaded files.
    """

    if not driver.current_url.startswith("chrome://downloads"):
        driver.get("chrome://downloads/")
    return driver.execute_script("""
        var items = downloads.Manager.get().items_;
        if (items.every(e => e.state === "COMPLETE"))
            return items.map(e => e.fileUrl || e.file_url);
        """)


def get_next_pager_link(driver, page_index):
    """
    Return a Selenium-clickable link to the next page of results.

    When a search yields multiple pages of results, we want to be able to
    iterate through those multiple pages. This function uses Selenium to
    retrieve the next pager link.

    Why not just return all of the links and iterate through them? Because
    Selenium will throw a "stale element" exception after the next page load.

    Args:
        driver (webdriver): a Selenium webdriver.

        page_index (int): an index of the current page number, as reflected in
                          the results page's built-in pager. Starts from 1.

    Returns:
        A pager link (as a Selenium object) if successful, False otherwise.
    """
    # Try instead looking for link names that are [the next number] and continue
    # until Selenium can't find any more.

    # Sample HREF, this one for page 2:
    # javascript:__doPostBack(&#39;ctl00$MainContent$ucA133SearchResults$ResultsGrid&#39;,&#39;Page$2&#39;)

    link_to_next_page = False

    try:
        pager = driver.find_element_by_css_selector('tr.GridPager')
    except:
        # @todo: Consider whether an exception is actually the most appropriate way to handle this.
        # @todo: Also... consider that Selenium will already throw its own
        #        exception if it can't find the element. So probably you want to
        #        take a different approach, somehow.
        Exception(" No pager was found!")

    try:
        # @todo: Figure out how best to handle the likelihood of a
        #        NoSuchElementException. Will a simple 'if' statement get it?
        link_to_next_page = pager.find_element_by_link_text(str(page_index + 1))
    except:
        Exception(" No next page was found.")
        # @todo: Add the page number, to make this more useful for debugging.

    return link_to_next_page


def download_all_linked_files(driver):
    """
    Initiate downloads of all files -- currently, SF-SAC forms (as .xls files)
    and single audit packages (as individual PDFs) -- linked from a results page
    on the Federal Audit Clearinghouse.

    Args:
        driver (webdriver): a Selenium webdriver.

    Returns:
        True if successful, False otherwise

    Room for improvement:
        Safeguard against the possibilities that you aren't on the correct page,
        don't have any results, etc.

        (URL should be https://harvester.census.gov/facdissem/SearchResults.aspx)
    """

    try:
        download_one_set_of_result_files(driver, 'SF-SAC')
    except:
        # @todo: Make this error handling more informative. Add page numbers,
        #        for instance, and/or something more specific about the file
        #        that couldn't be downloaded.
        Exception(" The SF-SAC forms couldn't all be downloaded.")

    try:
        download_one_set_of_result_files(driver, 'PDF')
    except:
        # @todo: Ditto.
        # @todo: Think through how to most appropriately handle instances in
        #        which an SF-SAC form is linked but no single audit PDF is
        #        linked. This sometimes happens with, for instance, tribes.
        Exception(" The single audit PDFs couldn't all be downloaded.")


def download_one_set_of_result_files(driver, file_type):
    """
    Initiate downloads of one type of files linked from a results page on the
    Federal Audit Clearinghouse.

    Args:
        driver (webdriver): a Selenium webdriver.

        file_type (string): 'SF-SAC', corresponding to the SF-SAC form, or
                            'PDF', corresponding to a PDF of the single audit
                            package.

    Returns:
        True if successful, False otherwise

    Room for improvement:
        Safeguard against the possibilities that you aren't on the correct page,
        don't have any results, etc.

        (URL should be https://harvester.census.gov/facdissem/SearchResults.aspx)
    """

    # If you have to select something else first, try either the DIV with ID
    # "MainContent_ucA133SearchResults_UpdatePanel"
    # or the table with ID "MainContent_ucA133SearchResults_ResultsGrid".
    #
    # (The div contains the table contains the cells that contain these links.)

    # @todo: Ideally, count the results and iterate through them (so you can be
    #        guaranteed a reasonable amount of accuracy and flexibility), but
    #        I'm going to start by brute-forcing it: check whether the link
    #        exists, and if it does, click it.

    # ex: 'a' element with ID "MainContent_ucA133SearchResults_ResultsGrid_lnkbuttonForm_0"
    #
    # @todo: Consider refactoring the (single audit) PDF download such that it
    #        happens in tandem with this. 'Cause there's a corresponding audit
    #        link (MainContent_ucA133SearchResults_ResultsGrid_lnkbuttonAudit_0)
    #        for each form (MainContent_ucA133SearchResults_ResultsGrid_lnkbuttonForm_0)
    #
    #        ...and then you could have greater parallelism and be able to give
    #        better insight into how far along the downloads are.
    #
    #        (Would you still get the grantee names in a lookup file, though?
    #        Probably not, in which case you'd want to assign the files a
    #        different filename based on the contents of this search-results-
    #        page table... could get awkward, but it's something to consider.)

    # @todo: Consider reworking this such that 'Form' and 'Audit' are the
    #        expected values. It's a question of readability.
    if file_type == 'SF-SAC':
        name_suffix = 'Form'
    elif file_type == 'PDF':
        name_suffix = 'Audit'
    else:
        return False

    max_number_of_search_results = 25  # numbered 0 through 24. As mentioned above, it'd be good to make this more flexible.
    for i in range(max_number_of_search_results):
        # Try to locate the relevant download link.
        link_name = 'MainContent_ucA133SearchResults_ResultsGrid_lnkbutton' + name_suffix + '_' + str(i)

        try:
            download_link = driver.find_element_by_id(link_name)
        except:
            Exception(" Selenium couldn't find that element.")  # @todo: Improve this to include the link_name.

        download_link.click()

    # @todo: Return True or False more thoughtfully.
    return True


def download_files_from_fac(agency_prefix=None, subagency_extension=None):
    """
    Search the Federal Audit Clearinghouse for relevant single audits, then
    download the results.

    Args:
        agency_prefix (string): a string representation of a two-digit integer
                                corresponding to a federal agency. These can be
                                found on the Federal Audit Clearinghouse itself.

        subagency_extension (string): a string representation of a one-digit
                                      integer representing a subagency's prefix.
                                      @todo: Replace this with a direct CFDA
                                             lookup soon, having learned that
                                             though subagencies' prefixes
                                             reliably map to CFDA numbers in
                                             some agencies, that's not the case
                                             in all agencies.

    Returns:
        An HttpResponse. Also, if successful, initiates a set of downloads.
    """

    # Setting subagency_extension default to DOT FTA for demo purposes.
    if agency_prefix is None or type(agency_prefix) is not str:
        agency_prefix = DEPT_OF_TRANSPORTATION_PREFIX

    if subagency_extension is None:
        subagency_extension = FTA_SUBAGENCY_CODE

    check_for_chromedriver()

    driver = webdriver.Chrome(CHROME_DRIVER_LOCATION)  # Optional argument, if not specified will search path.

    # 1. Go to the Federal Audit Clearinghouse's search page.
    driver.get(FAC_URL)
    time.sleep(2)  # ...just in case.

    # 2. Click the “General Information” accordion. Otherwise Selenium will
    #    throw an "Element Not Interactable" exception.
    driver.find_element_by_id('ui-id-1').click()

    # 3. To get all recent results, enter [90 days ago] and today into the
    #    “FAC Release Date” fields (“From” and “To,” respectively).
    from_date_field = driver.find_element_by_id('MainContent_UcSearchFilters_DateProcessedControl_FromDate')

    from_date = _calculate_start_date(90)
    from_date_field.clear()
    from_date_field.send_keys(from_date)
    from_date_field.send_keys(Keys.RETURN)

    # Set the "to" date to yesterday, not today, to avoid time zone problems.
    # @todo: https://github.com/18F/federal-grant-reporting/issues/146
    yesterday = date.today() - timedelta(1)
    to_date = _format_date_for_fac_fields(yesterday)
    to_date_field = driver.find_element_by_id('MainContent_UcSearchFilters_DateProcessedControl_ToDate')
    to_date_field.clear()
    to_date_field.send_keys(to_date)
    to_date_field.send_keys(Keys.RETURN)

    # 4. Click the ‘Federal Awards’ accordion, so the elements under it will be
    #    'interactable.'
    driver.find_element_by_id('ui-id-5').click()

    # 5. Search by CFDA number:
    driver.find_element_by_id('MainContent_UcSearchFilters_CDFASelectionControl_SelectionControlTable')
    cfda_select = Select(driver.find_element_by_id('cfdaPrefix'))

    # Select the option whose value (mercifully) matches the agency prefix.
    cfda_select.select_by_value(agency_prefix)

    # Enter suffix/additional search
    cfda_extension = driver.find_element_by_id('cfdaExt')
    cfda_extension.clear()
    cfda_extension.send_keys(subagency_extension)
    # Don't send 'Keys.RETURN' here, otherwise the entire form will get
    # submitted instead of filling out the remainder first.

    # Click the 'includes' checkbox. Otherwise you'd need to enter exact matches.
    time.sleep(1)  # ...just in case. There's likely a more elegant way to handle this.
    driver.find_element_by_id('cfdaContains').click()

    # Add the filter. (It won't happen automatically.)
    driver.find_element_by_id('btnAdd').click()

    # 7. Click the ‘Search’ button.
    driver.find_element_by_id('MainContent_UcSearchFilters_Panel4')  # in case you just need to break it out of the focus on the accordions?
    driver.find_element_by_id('MainContent_UcSearchFilters_btnSearch_bottom').click()

    # 8. A new page loads. Click the ‘I acknowledge that I have read and
    #    understand the above statements’ checkbox.
    time.sleep(1)

    driver.find_element_by_id('chkAgree').click()

    # @todo: Replace this with a better 'wait', but the point is to make sure
    #        the button has loaded and can be clicked:
    time.sleep(1)

    # 9. Click the ‘Continue to Search Results’ button.
    driver.find_element_by_id('btnIAgree').click()

    time.sleep(1)

    # 10. Run through and download all linked results (SF-SAC forms and single
    #     audit PDFs).

    download_all_linked_files(driver)

    current_page_index = 1

    link_to_next_page = get_next_pager_link(driver, current_page_index)

    while link_to_next_page:
        # i.e., until there are no more pager links available:
        link_to_next_page.click()
        current_page_index += 1  # @todo: Add better error checking.

        # @todo: Make this wait more intelligent.
        time.sleep(1)

        # @todo: Now that you're not downloading a ZIP file that includes a
        #        cross-reference spreadsheet, add a different way to retrieve
        #        and use the number-to-awardee-name[-and-fiscal-year] crosswalk.
        download_all_linked_files(driver)

        link_to_next_page = get_next_pager_link(driver, current_page_index)

    # Wait for download(s) to complete, then gets their paths.
    # @todo: Consider whether saving and returning the paths is overkill.
    paths = WebDriverWait(driver, 500, 1).until(list_completed_chrome_downloads)

    if paths:
        driver.quit()

    # @todo: Improve the contents of this HttpResponse.
    return HttpResponse("Your download has completed.", content_type="text/plain")


def prompt_for_agency_name(request):
    if request.method == 'POST':
        form = AgencySelectionForm(request.POST)

        if form.is_valid():
            #cd = form.cleaned_data
            #agency_prefix = cd['agency']
            # @todo: Run the calculations here instead?
            pass

    else:
        form = AgencySelectionForm()

    return render(request, 'distiller/index.html', {'form': form})


def __get_findings(agency_df):
    """
    Args:
        A dataframe of agency data, currently derived from genXX.txt.

    Returns:
        A dataframe of findings, or 'None'.

    Room for improvement:
        Modify this function to retrieve the cross-referenced findings instead
        of just 'Y/N'.
    """

    try:
        findings_df = agency_df.loc[agency_df['CYFINDINGS'] == 'Y']
        return findings_df

    except:
        # @todo: Figure out what exception to actually raise here.
        Exception(" Error generating findings dataframe.")


def __get_number_of_findings(agency_df):
    """
    Args:
        agency_df: A dataframe of agency data, currently derived from genXX.txt.

    Returns:
        An integer, or 'None'.
    """

    try:
        findings_df = __get_findings(agency_df)
        return len(findings_df.index)

    except:
        Exception(" Error getting number of findings.")


def filter_general_table_by_agency(agency_prefix, filename="gen18.txt"):
    actual_filename = files_directory + '/' + filename

    # Not using the index for anything, so let's leave it arbitrary for now.
    df = pd.read_csv(actual_filename, low_memory=False, encoding='latin-1')

    agency_df = df.loc[df['COGAGENCY'] == agency_prefix]

    return agency_df


# @todo: clean up the naming here.
def generate_csv_download(dataframe, results_filename='agency-specific-results.csv'):
    # Use a buffer so we can prompt the user to download the file.
    new_csv = StringIO()

    dataframe.to_csv(new_csv, encoding='utf-8', index=False)
    # Rewind the buffer so we don't get a zero-length error.
    new_csv.seek(0)

    response = HttpResponse(new_csv, content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="%s"' % results_filename

    return response


def offer_download_of_agency_specific_csv(request, agency_prefix=DEPT_OF_TRANSPORTATION_PREFIX):
    agency_df = filter_general_table_by_agency(agency_prefix)

    response = generate_csv_download(agency_df)

    return response


def derive_agency_highlights(agency_prefix, filename='gen18.txt'):
    agency_df = filter_general_table_by_agency(agency_prefix)

    highlights = {  # or "overview"
        'agency_prefix': agency_prefix,
        'agency_name': __get_agency_name_from_prefix(agency_prefix),
        'filename': filename,
        'results': {
            'cognizant_sum': len(agency_df.index),
            'findings': __get_number_of_findings(agency_df),
        }
        # 'cog_or_oversight': [_____]  # @todo: Think through and add this later.
    }

    return highlights


def show_agency_level_summary(request):
    agency_prefix = request.POST['agency']
    try:
        __is_valid_agency_prefix(agency_prefix)
        highlights = derive_agency_highlights(agency_prefix)

        return render(request, 'distiller/results.html', highlights)

    except:
        ValueError("That doesn't seem to be a valid federal agency prefix.")


def extract_findings_from_pdf():
    # @todo: Rework this to actually show something. For now, just log to console.
    # @todo: Rework this to be more dynamic. For now, start with parsing just
    # one PDF and expand from there.

    findings = True

    return findings
