import unittest

import numpy as np
import pandas as pd

from dna import utils
from dna import metrics  


class MetricsTestCase(unittest.TestCase):

    def format_top_k(self, scores, ids, rank):
        """
        A helper function for the top k tests.
        """
        actual_data = pd.DataFrame({'test_f1_macro': scores, 'pipeline_id': ids})
        ranked_data = pd.DataFrame({'rank': rank, 'pipeline_id': ids})
        return ranked_data, actual_data

    def format_and_get_top_k(self, scores, ids, rank, k, top_k_function):
        """
        :param scores: the `scores` of the pipelines in a list
        :param ids: a list of the pipeline ids
        :param rank: a list of the pipeline rankings
        :param k: the number of pipelines to get
        :param top_k_function: the top k function to evaluate, top_k_regret or top_k_correct
        """
        ranked_data, actual_data = self.format_top_k(scores, ids, rank)
        metric = top_k_function(ranked_data, actual_data, k)
        return metric

    def format_and_get_spearman(self, actual_data, ranked_data):
        """
        :param actual_data: a list of the real rankings
        :param ranked_data: a list of the ranked predictions
        """
        actual_data.reverse() # our rank function in spearman will reverse this back
        actual_data = pd.DataFrame({'test_f1_macro': actual_data})
        ranked_data = pd.DataFrame({'rank': ranked_data})
        metric = metrics.spearman_correlation(ranked_data, actual_data)
        return metric

    def test_rmse(self):
        values_pred = [.5]
        values_actual = [0]
        true_value = (.5**2)**.5
        metric = metrics.rmse(values_pred, values_actual)
        np.testing.assert_almost_equal(metric, true_value, err_msg='failed to get rmse of one item')

        values_pred = [.1, .2, .3, .4, .5]
        values_actual = [.1, .2, .3, .4, .5]
        true_value = 0
        metric = metrics.rmse(values_pred, values_actual)
        np.testing.assert_almost_equal(metric, true_value, err_msg='failed to get rmse of perfect')

        values_pred = [.1, .2, .3, .4, .5]
        values_actual = [.2, .3, .4, .5, .6]
        true_value = (.1**2)**.5
        metric = metrics.rmse(values_pred, values_actual)
        np.testing.assert_almost_equal(metric, true_value, err_msg='failed to get rmse of mixed values')

    def test_accuracy(self):
        values_pred = [1]
        values_actual = [0]
        true_value = 0
        metric = metrics.accuracy(values_pred, values_actual)
        np.testing.assert_almost_equal(metric, true_value, err_msg='failed to get accuracy of one item')

        values_pred = [1, 1, 1, 1, 1]
        values_actual = [1, 1, 1, 1, 1]
        true_value = 1
        metric = metrics.accuracy(values_pred, values_actual)
        np.testing.assert_almost_equal(metric, true_value, err_msg='failed to get accuracy of perfect')

        values_pred = [1, 1, 1, 1, 1]
        values_actual = [0, 1, 0, 1, 1]
        true_value = .6
        metric = metrics.accuracy(values_pred, values_actual)
        np.testing.assert_almost_equal(metric, true_value, err_msg='failed to get accuracy of mixed values')

    def test_spearman(self):
        """
        Example #1 from SciPy at https://github.com/scipy/scipy/blob/master/scipy/stats/stats.py line 3669
        Example #2 from https://chrisalbon.com/statistics/frequentist/spearmans_rank_correlation/

        We have to pass the values to spearman and it will rank them OR we pass the ranked values where #1 is the lowest
        """       
        metric = self.format_and_get_spearman([1,2,3,4,5], [5,6,7,8,7])
        true_metric = (0.82078268166812329, 0.088587005313543798)
        np.testing.assert_almost_equal(metric, true_metric, 
                                       err_msg='failed to get spearman from scipy, was {}, shouldve been {}'.format(metric, true_metric))
        
        metric = self.format_and_get_spearman([1,2,3,4,5,6,7,8,9], [2,1,2,4.5,7,6.5,6,9,9.5])
        true_metric = 0.90377360145618091
        np.testing.assert_almost_equal(metric[0], true_metric, 
                                       err_msg='failed to get spearman from second example, was {}, shouldve been {}'.format(metric, true_metric))
        

        metric = self.format_and_get_spearman([1,2,3], [2,4,6])
        true_metric = (1.0, 0)
        np.testing.assert_almost_equal(metric, true_metric, 
                                       err_msg='failed to get spearman from perfect example, was {}, shouldve been {}'.format(metric, true_metric))

        
        metric = self.format_and_get_spearman([3, 2, 1],[2, 4, 6] )
        true_metric = (-1.0, 0)
        np.testing.assert_almost_equal(metric, true_metric, 
                                       err_msg='failed to get spearman from inverse example, was {}, shouldve been {}'.format(metric, true_metric))

        random_n = 2000000  # need 2 million to match 3 decimals consistently. More takes took long
        metric = self.format_and_get_spearman(list(np.random.rand(random_n)),list(np.random.rand(random_n)))
        true_metric = 0
        np.testing.assert_almost_equal(metric[0], true_metric, decimal=3,
                                       err_msg='failed to get spearman from random example, was {}, shouldve been {}'.format(metric, true_metric))
        assert metric[1] > .1, 'pvalue for random was too significant for random: {}'.format(metric[1])


    def test_pearson_correlation(self):
        """
        Examples #4 from https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.pearsonr.html
        Since our code is literally the same as theirs, this is just a sanity check
        """
        values_pred = [.3, .4, .5]
        values_actual = [.3, .4, .5]
        true_value = 1
        metric = metrics.pearson_correlation(values_pred, values_actual)
        np.testing.assert_almost_equal(metric[0], true_value, err_msg='failed to get Pearson of perfect')

        values_pred = [.5, .4, .3]
        values_actual = [.3, .4, .5]
        true_value = -1
        metric = metrics.pearson_correlation(values_pred, values_actual)
        np.testing.assert_almost_equal(metric[0], true_value, err_msg='failed to get Pearson of inverse')

        values_pred = [5., .4, .3]
        values_actual = [1, 1, 1]
        true_value = np.nan  # in the limit defined as nan
        metric = metrics.pearson_correlation(values_pred, values_actual)
        np.testing.assert_almost_equal(metric[0], true_value, err_msg='failed to get Pearson of NaN')

        true_value = (-0.7426106572325057, 0.1505558088534455)
        metric = metrics.pearson_correlation( [1, 2, 3, 4, 5], [10, 9, 2.5, 6, 4])
        np.testing.assert_almost_equal(metric, true_value, err_msg='failed to get Pearson of NaN')

    
    def test_top_k_correct(self):
        k = 1
        metric = self.format_and_get_top_k(scores=[0, .5, 1], ids=[0, 1, 2], rank=[1, 2, 3], k=k, top_k_function=metrics.top_k_correct)
        true_metric = 0
        np.testing.assert_almost_equal(metric, true_metric, 
                                       err_msg='failed to get top_1 ranking from 0 example: was {}, shouldve been {}'.format(metric, true_metric))


        metric = self.format_and_get_top_k(scores=[0, .5, 1], ids=[0, 1, 2], rank=[3, 2, 1], k=k, top_k_function=metrics.top_k_correct)
        true_metric = 1
        np.testing.assert_almost_equal(metric, true_metric, 
                                       err_msg='failed to get top_1 ranking from perfect example: was {}, shouldve been {}'.format(metric, true_metric))
        
        k = 3
        metric = self.format_and_get_top_k(scores=[0, .5, 1], ids=[0, 1, 2], rank=[3, 2, 1], k=k, top_k_function=metrics.top_k_correct)
        true_metric = 3
        np.testing.assert_almost_equal(metric, true_metric, 
                                       err_msg='failed to get top_3 ranking from perfect example: was {}, shouldve been {}'.format(metric, true_metric))


    def test_top_k_regret(self):
        k = 1
        metric = self.format_and_get_top_k(scores=[0, .5, 1], ids=[0, 1, 2], rank=[1, 2, 3], k=k, top_k_function=metrics.top_k_regret)
        true_metric = 1
        np.testing.assert_almost_equal(metric, true_metric, 
                                       err_msg='failed to get top_1 regret: was {}, shouldve been {}'.format(metric, true_metric))


        metric = self.format_and_get_top_k(scores=[0, .5, 1], ids=[0, 1, 2], rank=[3, 2, 1], k=k, top_k_function=metrics.top_k_regret)
        true_metric = 0
        np.testing.assert_almost_equal(metric, true_metric, 
                                       err_msg='failed to get top_1 ranking from perfect example: was {}, shouldve been {}'.format(metric, true_metric))
        
        k = 2
        metric = self.format_and_get_top_k(scores=[0, .5, 1], ids=[0, 1, 2], rank=[1, 2, 3], k=k, top_k_function=metrics.top_k_regret)
        true_metric = .5  # the smallest difference between the highest actual one f1_macro and the highest predicted k
        np.testing.assert_almost_equal(metric, true_metric, 
                                       err_msg='failed to get top_3 ranking from example: was {}, shouldve been {}'.format(metric, true_metric))


            